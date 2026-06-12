import streamlit as st
import pandas as pd
import numpy as np
from fpdf import FPDF
import datetime
import urllib.request
import json

# --- CONFIGURACIÓN VISUAL ---
st.set_page_config(page_title="Planificador Ruston", layout="wide", page_icon="⚙️")

st.markdown("""
    <style>
    .kpi-card { background-color: white; padding: 20px; border-radius: 10px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1); border-left: 5px solid; }
    .kpi-title { font-size: 14px; color: #64748B; font-weight: 600; text-transform: uppercase; }
    .kpi-value { font-size: 28px; font-weight: 700; margin-top: 5px; }
    </style>
""", unsafe_allow_html=True)

# --- 1. CONEXIÓN API DÓLAR (BLINDADA) ---
@st.cache_data(ttl=3600)
def obtener_cotizaciones_historicas_api():
    url = "https://api.argentinadatos.com/v1/cotizaciones/dolares/oficial"
    try:
        # Usamos urllib con User-Agent para evitar que la API bloquee a Streamlit
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
        
        df_tc = pd.DataFrame(data)
        df_tc['fecha'] = pd.to_datetime(df_tc['fecha'])
        df_tc = df_tc[['fecha', 'venta']].rename(columns={'venta': 'Cotizacion'}).sort_values('fecha')
        return df_tc
    except Exception:
        # Falla silenciosa: si la API se cae, retorna None para usar el plan B
        return None

# --- 2. MOTOR DE DATOS CENTRAL (LECTURA LOCAL) ---
@st.cache_data(ttl=600)
def cargar_arquitectura_datos():
    try:
        # A. Procesar Compras (Autodetecta el separador y omite filas corruptas)
        df_compras = pd.read_csv('Ultima_Compra.csv', sep=None, engine='python', on_bad_lines='skip')
        df_compras['FecCreacion'] = pd.to_datetime(df_compras['FecCreacion'], errors='coerce')
        df_compras = df_compras.dropna(subset=['FecCreacion']).sort_values('FecCreacion')
        
        # B. Aplicar Tipo de Cambio
        df_tc = obtener_cotizaciones_historicas_api()
        if df_tc is not None:
            df_compras = pd.merge_asof(df_compras, df_tc, left_on='FecCreacion', right_on='fecha', direction='backward')
            df_compras['Costo_Unitario'] = np.where(df_compras['Moneda'] == 'USD', df_compras['ValorUnidad'], df_compras['ValorUnidad'] / df_compras['Cotizacion'])
        else:
            st.warning("⚠️ La API del BCRA está inactiva. Usando Tipo de Cambio fijo referencial (1000 ARS/USD).")
            df_compras['Costo_Unitario'] = np.where(df_compras['Moneda'] == 'USD', df_compras['ValorUnidad'], df_compras['ValorUnidad'] / 1000)
            
        df_compras = df_compras.sort_values('FecCreacion', ascending=False)
        df_mat_precios = df_compras.drop_duplicates(subset=['CodMaterial'], keep='first').copy()
        
        # C. Procesar Textos de Materiales
        df_textos = pd.read_csv('materiales.csv', sep=None, engine='python', on_bad_lines='skip').drop_duplicates(subset=['CodMaterial'], keep='last')
        df_mat_maestro = pd.merge(df_mat_precios, df_textos[['CodMaterial', 'DescMaterial']], on='CodMaterial', how='left')
        df_mat_maestro = df_mat_maestro.rename(columns={'CodMaterial': 'Codigo', 'DescMaterial': 'Descripcion'})
        df_mat_maestro = df_mat_maestro[['Codigo', 'Descripcion', 'Costo_Unitario']]
        
        # D. Procesar Libro de Configuración Manual (Excel)
        df_tareas_base = pd.read_excel('Configuracion_Overhaul.xlsx', sheet_name='Tareas_Base')
        df_comp_mayores = pd.read_excel('Configuracion_Overhaul.xlsx', sheet_name='Componentes_Mayores')
        df_srv_maestro = pd.read_excel('Configuracion_Overhaul.xlsx', sheet_name='Maestra_Servicios')
        df_maestra_componentes = pd.read_excel('Configuracion_Overhaul.xlsx', sheet_name='Maestra_Componentes')
        
        df_srv_maestro = df_srv_maestro.rename(columns={'Codigo_Servicio': 'Codigo', 'Descripcion_Servicio': 'Descripcion', 'Tarifa_Unitaria': 'Costo_Unitario'})
        
        return df_mat_maestro, df_srv_maestro, df_tareas_base, df_comp_mayores, df_maestra_componentes
    except Exception as e:
        st.error(f"Error procesando los datos. Verifica que los 3 archivos estén en GitHub. Detalle técnico: {e}")
        st.stop()

# Cargar las tablas maestras
df_materiales, df_servicios, df_tareas_base, df_comp_mayores, df_maestra_componentes = cargar_arquitectura_datos()

# --- INTERFAZ DE USUARIO ---
with st.sidebar:
    st.markdown("### Ruston Planner")
    st.markdown("---")
    proyecto_nombre = st.text_input("Orden de Trabajo / Proyecto:", value="TC02 - La Paz")
    
    st.markdown("<br>**Órdenes de Trabajo Base**", unsafe_allow_html=True)
    cambio_modulo = st.toggle("Cambio de Módulo", value=True)
    intervencion_tp = st.toggle("Intervención de TP", value=False)
    intervencion_torpedo = st.toggle("Overhaul de Torpedo", value=False)

st.header("Cotizador de Mantenimiento Mayor")
st.caption(f"Fecha de simulación: {datetime.date.today().strftime('%d de %B, %Y')} | Origen de datos: SAP / Repositorio Interno")

detalles_presupuesto = []

# --- 3. CÁLCULO DE TAREAS BASE ---
intervenciones_activas = []
if cambio_modulo: intervenciones_activas.append("Cambio de módulo")
if intervencion_tp: intervenciones_activas.append("Intervención de TP")
if intervencion_torpedo: intervenciones_activas.append("Intervención de torpedo")

for intervencion in intervenciones_activas:
    items = df_tareas_base[df_tareas_base['ID_Intervencion'] == intervencion]
    for _, fila in items.iterrows():
        cod = fila['Codigo_Elemento']
        tipo = fila['Tipo_Posicion']
        cant = fila['Cantidad']
        
        if tipo == 'Material':
            info = df_materiales[df_materiales['Codigo'] == cod]
        else:
            info = df_servicios[df_servicios['Codigo'] == cod]
            
        if not info.empty:
            detalles_presupuesto.append({
                "Categoría": "Tarea Base", "Origen": intervencion, "Codigo_SAP": cod,
                "Descripcion": info.iloc[0]['Descripcion'], "Tipo": tipo,
                "Cantidad": cant, "Costo_Unitario": info.iloc[0]['Costo_Unitario'],
                "Subtotal": cant * info.iloc[0]['Costo_Unitario']
            })

# --- 4. SELECCIÓN DE COMPONENTES MAYORES ---
st.markdown("### Componentes Mayores a Reemplazar")
tab1, tab2, tab3 = st.tabs(["Compresor Axial", "Sistema de Combustión", "Turbina de Potencia"])
componentes_seleccionados = []

with tab1:
    col1, col2 = st.columns(2)
    with col1:
        if st.checkbox("Álabes estatóricos de compresor axial"): componentes_seleccionados.append("Álabes estatóricos de compresor axial")
    with col2:
        if st.checkbox("Álabes rotóricos de compresor axial"): componentes_seleccionados.append("Álabes rotóricos de compresor axial")

with tab2:
    col1, col2 = st.columns(2)
    with col1:
        if st.checkbox("Tubos de llama"): componentes_seleccionados.append("Tubos de llama")
        if st.checkbox("Tubos de extensión"): componentes_seleccionados.append("Tubos de extensión")
    with col2:
        st.markdown("**Cuadrantes de Cámara**")
        estado_cuadrantes = st.radio("Condición:", ["No reemplazar", "Nuevos (de fábrica)", "Recuperados (Refurbished)"], horizontal=True, label_visibility="collapsed")
        if estado_cuadrantes == "Nuevos (de fábrica)": componentes_seleccionados.append("Cuadrantes nuevos")
        elif estado_cuadrantes == "Recuperados (Refurbished)": componentes_seleccionados.append("Cuadrantes reparados")

with tab3:
    col1, col2 = st.columns(2)
    with col1:
        if st.checkbox("Toberas de primera"): componentes_seleccionados.append("Toberas de primera")
        if st.checkbox("Estator de segunda"): componentes_seleccionados.append("Estator de segunda")
        if st.checkbox("Estator de primera de TP"): componentes_seleccionados.append("Estator de primera de TP")
        if st.checkbox("Estator de segunda de TP"): componentes_seleccionados.append("Estator de segunda de TP")
    with col2:
        if st.checkbox("Alabes de primera etapa rotórica"): componentes_seleccionados.append("Alabes de primera etapa rotórica")
        if st.checkbox("Alabes de segunda etapa rotórica"): componentes_seleccionados.append("Alabes de segunda etapa rotórica")
        if st.checkbox("Rotor de primera de TP"): componentes_seleccionados.append("Rotor de primera de TP")
        if st.checkbox("Rotor de segunda de TP"): componentes_seleccionados.append("Rotor de segunda de TP")

# --- 5. CÁLCULO DE COMPONENTES MAYORES ---
for comp_padre in componentes_seleccionados:
    comp_info = df_maestra_componentes[df_maestra_componentes['Nombre_Pantalla'] == comp_padre]
    if not comp_info.empty:
        id_comp = comp_info.iloc[0]['ID_Componente']
        hijos = df_comp_mayores[df_comp_mayores['ID_Componente'] == id_comp]
        
        for _, fila in hijos.iterrows():
            cod = fila['Codigo_Elemento']
            tipo = fila['Tipo_Posicion']
            cant = fila['Cantidad']
            
            info = df_materiales[df_materiales['Codigo'] == cod] if tipo == 'Material' else df_servicios[df_servicios['Codigo'] == cod]
                
            if not info.empty:
                detalles_presupuesto.append({
                    "Categoría": "Componente Mayor", "Origen": comp_padre, "Codigo_SAP": cod,
                    "Descripcion": info.iloc[0]['Descripcion'], "Tipo": tipo,
                    "Cantidad": cant, "Costo_Unitario": info.iloc[0]['Costo_Unitario'],
                    "Subtotal": cant * info.iloc[0]['Costo_Unitario']
                })

# --- 6. RESUMEN Y GENERACIÓN DE PDF ---
st.markdown("---")

if detalles_presupuesto:
    df_resumen = pd.DataFrame(detalles_presupuesto)
    costo_total = df_resumen['Subtotal'].sum()
    total_mat = df_resumen[df_resumen["Tipo"]=="Material"]["Subtotal"].sum()
    total_srv = df_resumen[df_resumen["Tipo"]=="Servicio"]["Subtotal"].sum()
    
    col_kpi1, col_kpi2, col_kpi3 = st.columns(3)
    with col_kpi1: st.markdown(f'<div class="kpi-card" style="border-color: #2563EB;"><div class="kpi-title">Costo Total Estimado</div><div class="kpi-value" style="color: #2563EB;">USD {costo_total:,.2f}</div></div>', unsafe_allow_html=True)
    with col_kpi2: st.markdown(f'<div class="kpi-card" style="border-color: #10B981;"><div class="kpi-title">Total Materiales</div><div class="kpi-value" style="color: #10B981;">USD {total_mat:,.2f}</div></div>', unsafe_allow_html=True)
    with col_kpi3: st.markdown(f'<div class="kpi-card" style="border-color: #F59E0B;"><div class="kpi-title">Total Servicios / MO</div><div class="kpi-value" style="color: #F59E0B;">USD {total_srv:,.2f}</div></div>', unsafe_allow_html=True)
        
    st.write("")
    st.markdown("### Desglose Financiero")
    st.dataframe(df_resumen[['Codigo_SAP', 'Descripcion', 'Tipo', 'Origen', 'Cantidad', 'Costo_Unitario', 'Subtotal']].style.format({"Costo_Unitario": "{:,.2f}", "Subtotal": "{:,.2f}"}), use_container_width=True)
    
    class PDF(FPDF):
        def header(self):
            self.set_fill_color(15, 23, 42)
            self.rect(0, 0, 210, 25, 'F')
            self.set_font('Helvetica', 'B', 14)
            self.set_text_color(255, 255, 255)
            self.cell(0, 8, 'REPORTE DE COSTOS: OVERHAUL RUSTON', ln=True)
            self.set_font('Helvetica', '', 10)
            self.cell(0, 5, 'Generado desde Simulador Integrado', ln=True)
            self.ln(15)
            
    pdf = PDF()
    pdf.add_page()
    pdf.set_font('Helvetica', 'B', 11)
    pdf.set_text_color(30, 41, 59)
    pdf.cell(0, 6, f"Orden de Trabajo / Proyecto: {proyecto_nombre}", ln=True)
    pdf.set_font('Helvetica', '', 10)
    pdf.cell(0, 6, f"Fecha de emisión: {datetime.date.today().strftime('%d/%m/%Y')}", ln=True)
    pdf.ln(5)
    
    pdf.set_font('Helvetica', 'B', 8)
    pdf.set_fill_color(241, 245, 249)
    col_w = [22, 70, 50, 15, 33]
    headers = ['Cód. SAP', 'Descripción', 'Instancia', 'Cant.', 'Subtotal (USD)']
    for w, h in zip(col_w, headers): pdf.cell(w, 7, h, 1, 0, 'C', True)
    pdf.ln()
    
    pdf.set_font('Helvetica', '', 8)
    for _, r in df_resumen.iterrows():
        pdf.cell(col_w[0], 6, str(r['Codigo_SAP']), 1, 0, 'C')
        pdf.cell(col_w[1], 6, r['Descripcion'][:45], 1)
        pdf.cell(col_w[2], 6, r['Origen'][:35], 1)
        pdf.cell(col_w[3], 6, str(int(r['Cantidad'])), 1, 0, 'C')
        pdf.cell(col_w[4], 6, f"{r['Subtotal']:,.2f}", 1, 1, 'R')
        
    pdf.ln(5)
    pdf.set_font('Helvetica', 'B', 11)
    pdf.set_fill_color(15, 23, 42)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(sum(col_w[:-1]), 10, 'COSTO TOTAL ESTIMADO: ', 1, 0, 'R', True)
    pdf.cell(col_w[-1], 10, f"{costo_total:,.2f}", 1, 1, 'R', True)

    with st.sidebar:
        st.markdown("---")
        st.download_button("📥 Generar PDF Oficial", bytes(pdf.output(dest='S')), f"Cotizacion_{proyecto_nombre.replace(' ', '_')}.pdf", "application/pdf", use_container_width=True)
else:
    st.info("💡 Por favor, configura las órdenes base o selecciona componentes en el panel para ver el desglose.")
