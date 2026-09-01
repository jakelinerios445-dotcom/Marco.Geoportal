```python
import requests
import pandas as pd
import numpy as np
import streamlit as st
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ------------------------------------------------------------------
# CONFIGURACIÓN GENERAL
# ------------------------------------------------------------------

# Coordenadas por defecto:
# Institución Universitaria Pascual Bravo
LAT_DEFECTO = 6.2766
LON_DEFECTO = -75.5901

# La URL del Geoportal que estamos usando es:
# https://marco.cornare.gov.co/geoportal/35
#
# El número 35 corresponde a la estación seleccionada.
# Para obtener los datos se continúa usando el endpoint de la API.
API_BASE_URL = "https://marco.cornare.gov.co/api/v1/estaciones"

LLAVE_FECHA = "level_date"
LLAVE_VALOR = "level"

CANDIDATOS_LAT = ["lat", "latitude", "latitud"]
CANDIDATOS_LON = ["lng", "lon", "longitude", "longitud"]

st.set_page_config(
    page_title="Análisis de niveles - CORNARE",
    page_icon="🌊",
    layout="wide"
)


# ------------------------------------------------------------------
# FUNCIONES DE CONSULTA
# ------------------------------------------------------------------

def obtener_serie_nivel(codigo_estacion, desde, hasta, calidad=1, timeout=30):
    """
    Consulta los niveles registrados por una estación de CORNARE.
    """

    url = f"{API_BASE_URL}/{codigo_estacion}/nivel"

    params = {
        "desde": desde,
        "hasta": hasta,
        "calidad": calidad
    }

    headers = {
        "User-Agent":
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36",

        "Accept": "application/json, text/plain, */*",
    }

    try:
        resp = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=timeout,
            verify=False
        )

        if resp.status_code == 200:
            return resp.json(), None

        return None, f"HTTP {resp.status_code}"

    except requests.exceptions.RequestException as e:
        return None, f"Error de red: {e}"


def obtener_todas_las_paginas(datos_json, timeout=30):
    """
    Recorre todas las páginas retornadas por la API.
    """

    registros = list(datos_json.get("values", []))
    siguiente_url = datos_json.get("next")

    while siguiente_url:

        try:
            resp = requests.get(
                siguiente_url,
                timeout=timeout,
                verify=False
            )

        except requests.exceptions.RequestException:
            break

        if resp.status_code != 200:
            break

        pagina = resp.json()

        registros.extend(
            pagina.get("values", [])
        )

        siguiente_url = pagina.get("next")

    return registros


# ------------------------------------------------------------------
# COORDENADAS
# ------------------------------------------------------------------

def detectar_coordenadas(datos_json):
    """
    Busca latitud y longitud dentro de la respuesta de la API.

    Si no existen, utiliza como referencia las coordenadas
    de la Institución Universitaria Pascual Bravo.
    """

    if not isinstance(datos_json, dict):
        return LAT_DEFECTO, LON_DEFECTO, False

    lat = next(
        (
            datos_json[k]
            for k in CANDIDATOS_LAT
            if k in datos_json
        ),
        None
    )

    lon = next(
        (
            datos_json[k]
            for k in CANDIDATOS_LON
            if k in datos_json
        ),
        None
    )

    if lat is not None and lon is not None:

        try:
            return float(lat), float(lon), True

        except (TypeError, ValueError):
            pass

    return LAT_DEFECTO, LON_DEFECTO, False


# ------------------------------------------------------------------
# DETECCIÓN DE OUTLIERS
# ------------------------------------------------------------------

def detectar_outliers(df):
    """
    Detecta valores atípicos mediante el método IQR.

    También se consideran inválidos niveles negativos.
    """

    if df.empty:
        return pd.Series(False, index=df.index)

    Q1 = df["nivel"].quantile(0.25)
    Q3 = df["nivel"].quantile(0.75)

    IQR = Q3 - Q1

    limite_inferior = Q1 - 1.5 * IQR
    limite_superior = Q3 + 1.5 * IQR

    outliers = (
        (df["nivel"] < limite_inferior)
        | (df["nivel"] > limite_superior)
        | (df["nivel"] < 0)
    )

    return outliers


# ------------------------------------------------------------------
# CÁLCULO DE HUECOS
# ------------------------------------------------------------------

def calcular_huecos(df):
    """
    Estima cuántos registros hacen falta según
    la frecuencia típica de medición.
    """

    if df.empty or len(df) < 2:
        return 0

    fechas = df["fecha"].sort_values()

    diferencias = fechas.diff().dropna()

    frecuencia_tipica = diferencias.mode()

    if len(frecuencia_tipica) == 0:
        return 0

    frecuencia_tipica = frecuencia_tipica.iloc[0]

    rango_completo = pd.date_range(
        start=fechas.min(),
        end=fechas.max(),
        freq=frecuencia_tipica
    )

    esperados = len(rango_completo)

    huecos = esperados - len(df)

    return max(0, int(huecos))


# ------------------------------------------------------------------
# ÍNDICE DE CALIDAD
# ------------------------------------------------------------------

def calcular_indice_calidad(df):
    """
    Índice de calidad entre 0 y 100.

    70 % = completitud de la serie
    30 % = datos sin valores atípicos
    """

    if df.empty or len(df) < 2:
        return 0.0, 0, 0

    huecos = calcular_huecos(df)

    frecuencia_tipica = df["fecha"].diff().dropna().mode()

    if len(frecuencia_tipica) == 0:
        return 0.0, huecos, 0

    frecuencia_tipica = frecuencia_tipica.iloc[0]

    rango_completo = pd.date_range(
        start=df["fecha"].min(),
        end=df["fecha"].max(),
        freq=frecuencia_tipica
    )

    esperados = len(rango_completo)

    if esperados > 0:
        completitud = max(
            0.0,
            1 - (huecos / esperados)
        )
    else:
        completitud = 0.0

    es_outlier = detectar_outliers(df)

    proporcion_outliers = es_outlier.mean()

    indice = (
        completitud * 0.70
        + (1 - proporcion_outliers) * 0.30
    ) * 100

    return (
        round(indice, 1),
        huecos,
        int(es_outlier.sum())
    )


# ------------------------------------------------------------------
# ENCABEZADO
# ------------------------------------------------------------------

st.title("🌊 Análisis de niveles de ríos y quebradas")

st.write(
    """
    Consulta y análisis de información hidrológica obtenida
    desde la plataforma **MARCO - CORNARE**.
    """
)

st.divider()


# ------------------------------------------------------------------
# SIDEBAR
# ------------------------------------------------------------------

with st.sidebar:

    st.header("⚙️ Configuración")

    st.write(
        "Selecciona los parámetros que quieres utilizar "
        "para realizar tu análisis."
    )

    st.divider()

    nombre_estudiante = st.text_input(
        "👤 Nombre del estudiante",
        "Tu Nombre Aquí"
    )

    codigo_estacion = st.text_input(
        "📍 Código de estación",
        "35",
        help="La estación asociada al Geoportal utilizado inicialmente es la 35."
    )

    fecha_desde_input = st.date_input(
        "📅 Fecha inicial",
        pd.to_datetime("2026-08-23")
    )

    fecha_hasta_input = st.date_input(
        "📅 Fecha final",
        pd.to_datetime("2026-08-30")
    )

    fecha_desde = fecha_desde_input.strftime("%Y-%m-%d")
    fecha_hasta = fecha_hasta_input.strftime("%Y-%m-%d")

    calidad = st.selectbox(
        "✅ Calidad de los datos",
        [1, 0],
        index=0,
        format_func=lambda x: (
            "Solo datos validados"
            if x == 1
            else "Todos los datos"
        )
    )

    st.divider()

    consultar = st.button(
        "🔎 CONSULTAR DATOS",
        type="primary",
        use_container_width=True
    )

    st.link_button(
        "🌐 Abrir Geoportal CORNARE",
        f"https://marco.cornare.gov.co/geoportal/{codigo_estacion}",
        use_container_width=True
    )


# ------------------------------------------------------------------
# INFORMACIÓN DE LA CONSULTA
# ------------------------------------------------------------------

col_info1, col_info2, col_info3 = st.columns(3)

with col_info1:
    st.info(f"👤 **Estudiante**\n\n{nombre_estudiante}")

with col_info2:
    st.info(f"📍 **Estación seleccionada**\n\n{codigo_estacion}")

with col_info3:
    st.info(
        f"📅 **Periodo solicitado**\n\n"
        f"{fecha_desde} → {fecha_hasta}"
    )


# ------------------------------------------------------------------
# CONSULTA Y PROCESAMIENTO
# ------------------------------------------------------------------

if consultar:

    if fecha_desde_input > fecha_hasta_input:

        st.error(
            "❌ La fecha inicial no puede ser posterior "
            "a la fecha final."
        )

        st.stop()

    with st.spinner("🌐 Consultando información de CORNARE..."):

        datos_crudos, error = obtener_serie_nivel(
            codigo_estacion,
            fecha_desde,
            fecha_hasta,
            calidad
        )

    if error:

        st.error(f"❌ No fue posible realizar la consulta: {error}")

    else:

        registros = obtener_todas_las_paginas(datos_crudos)

        if not registros:

            st.warning(
                "⚠️ No se encontraron registros para la estación "
                "y el rango de fechas seleccionados."
            )

        else:

            # ------------------------------------------------------
            # CREAR DATAFRAME ORIGINAL
            # ------------------------------------------------------

            df = pd.DataFrame(registros)

            df = df.rename(
                columns={
                    LLAVE_FECHA: "fecha",
                    LLAVE_VALOR: "nivel"
                }
            )

            df["fecha"] = pd.to_datetime(
                df["fecha"],
                errors="coerce"
            )

            df["nivel"] = pd.to_numeric(
                df["nivel"],
                errors="coerce"
            )

            df = (
                df
                .dropna(subset=["fecha", "nivel"])
                .sort_values("fecha")
                .reset_index(drop=True)
            )


            # ------------------------------------------------------
            # DETECTAR OUTLIERS
            # ------------------------------------------------------

            df["es_outlier"] = detectar_outliers(df)


            # ------------------------------------------------------
            # DATAFRAME SIN OUTLIERS
            # ------------------------------------------------------

            df_sin_outliers = (
                df[df["es_outlier"] == False]
                .copy()
                .reset_index(drop=True)
            )

            # Ya no necesitamos esta columna en el DataFrame limpio
            df_sin_outliers = df_sin_outliers.drop(
                columns=["es_outlier"]
            )


            # ------------------------------------------------------
            # ESTADÍSTICAS
            # ------------------------------------------------------

            indice_calidad, huecos, n_outliers = (
                calcular_indice_calidad(df)
            )

            huecos_sin_outliers = calcular_huecos(
                df_sin_outliers
            )

            lat, lon, coords_reales = detectar_coordenadas(
                datos_crudos
            )


            # ------------------------------------------------------
            # MENSAJE DE ÉXITO
            # ------------------------------------------------------

            st.success(
                f"✅ Consulta completada correctamente. "
                f"Se obtuvieron {len(df)} registros."
            )


            # ------------------------------------------------------
            # RESUMEN PRINCIPAL
            # ------------------------------------------------------

            st.subheader("📊 Resumen de la consulta")

            col1, col2, col3, col4 = st.columns(4)

            col1.metric(
                "Lecturas originales",
                len(df)
            )

            col2.metric(
                "Lecturas válidas",
                len(df_sin_outliers),
                delta=-n_outliers if n_outliers > 0 else 0
            )

            col3.metric(
                "Nivel promedio",
                f"{df['nivel'].mean():.2f}"
            )

            col4.metric(
                "Índice de calidad",
                f"{indice_calidad}/100"
            )


            col5, col6, col7, col8 = st.columns(4)

            col5.metric(
                "Outliers detectados",
                n_outliers
            )

            col6.metric(
                "Huecos originales",
                huecos
            )

            col7.metric(
                "Huecos sin outliers",
                huecos_sin_outliers
            )

            col8.metric(
                "Datos conservados",
                f"{(len(df_sin_outliers) / len(df) * 100):.1f}%"
            )


            # ------------------------------------------------------
            # EVIDENCIA DE DATOS PROPIOS
            # ------------------------------------------------------

            st.subheader("🧾 Evidencia de los datos utilizados")

            st.write(
                "Este resumen permite comprobar rápidamente que "
                "el análisis corresponde a la estación y al periodo "
                "seleccionados por el estudiante."
            )

            resumen = pd.DataFrame({
                "Característica": [
                    "Estación",
                    "Fecha inicial real",
                    "Fecha final real",
                    "Filas originales",
                    "Filas sin outliers",
                    "Outliers eliminados",
                    "Huecos originales",
                    "Huecos después de limpiar",
                    "Nivel mínimo original",
                    "Nivel máximo original",
                    "Nivel promedio original",
                    "Nivel promedio sin outliers"
                ],

                "Resultado": [
                    codigo_estacion,

                    df["fecha"].min().strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),

                    df["fecha"].max().strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),

                    len(df),

                    len(df_sin_outliers),

                    n_outliers,

                    huecos,

                    huecos_sin_outliers,

                    round(df["nivel"].min(), 2),

                    round(df["nivel"].max(), 2),

                    round(df["nivel"].mean(), 2),

                    round(
                        df_sin_outliers["nivel"].mean(),
                        2
                    )
                    if not df_sin_outliers.empty
                    else "Sin datos"
                ]
            })

            st.dataframe(
                resumen,
                use_container_width=True,
                hide_index=True
            )


            # ------------------------------------------------------
            # PESTAÑAS
            # ------------------------------------------------------

            tab1, tab2, tab3, tab4 = st.tabs([
                "📈 Serie temporal",
                "🧹 Limpieza de datos",
                "🗺️ Ubicación",
                "📋 Datos"
            ])


            # ------------------------------------------------------
            # TAB 1 - SERIE TEMPORAL
            # ------------------------------------------------------

            with tab1:

                st.subheader("Nivel registrado")

                st.write(
                    "Evolución del nivel registrado por la estación "
                    "durante el periodo seleccionado."
                )

                st.line_chart(
                    df.set_index("fecha")["nivel"],
                    use_container_width=True
                )

                st.subheader("Serie después de eliminar outliers")

                if not df_sin_outliers.empty:

                    st.line_chart(
                        df_sin_outliers
                        .set_index("fecha")["nivel"],
                        use_container_width=True
                    )

                else:

                    st.warning(
                        "No quedaron registros después "
                        "de eliminar los outliers."
                    )


            # ------------------------------------------------------
            # TAB 2 - LIMPIEZA
            # ------------------------------------------------------

            with tab2:

                st.subheader("🧹 Proceso de limpieza")

                col_a, col_b, col_c = st.columns(3)

                col_a.metric(
                    "Antes",
                    f"{len(df)} filas"
                )

                col_b.metric(
                    "Eliminadas",
                    f"{n_outliers} filas"
                )

                col_c.metric(
                    "Después",
                    f"{len(df_sin_outliers)} filas"
                )

                st.write(
                    """
                    Para detectar valores atípicos se utilizó el
                    **rango intercuartílico (IQR)**.

                    Se consideran outliers:

                    - Valores inferiores a `Q1 - 1.5 × IQR`.
                    - Valores superiores a `Q3 + 1.5 × IQR`.
                    - Niveles negativos.
                    """
                )

                if n_outliers > 0:

                    st.write("**Registros identificados como outliers:**")

                    st.dataframe(
                        df[df["es_outlier"] == True],
                        use_container_width=True,
                        hide_index=True
                    )

                else:

                    st.info(
                        "No se detectaron valores atípicos "
                        "en esta consulta."
                    )


            # ------------------------------------------------------
            # TAB 3 - MAPA
            # ------------------------------------------------------

            with tab3:

                st.subheader("🗺️ Ubicación de la estación")

                if not coords_reales:

                    st.warning(
                        "La respuesta de la API no contiene "
                        "coordenadas reconocibles. Por eso se muestra "
                        "como referencia la ubicación de la "
                        "Institución Universitaria Pascual Bravo."
                    )

                st.map(
                    pd.DataFrame({
                        "lat": [lat],
                        "lon": [lon]
                    }),
                    zoom=10
                )


            # ------------------------------------------------------
            # TAB 4 - DATOS
            # ------------------------------------------------------

            with tab4:

                st.subheader("📋 Datos originales")

                st.dataframe(
                    df,
                    use_container_width=True,
                    hide_index=True
                )

                st.subheader("✅ DataFrame sin outliers")

                st.dataframe(
                    df_sin_outliers,
                    use_container_width=True,
                    hide_index=True
                )


            # ------------------------------------------------------
            # DESCARGAS
            # ------------------------------------------------------

            st.divider()

            st.subheader("⬇️ Descargar resultados")

            col_descarga1, col_descarga2 = st.columns(2)

            csv_original = df.to_csv(
                index=False
            ).encode("utf-8")

            csv_limpio = df_sin_outliers.to_csv(
                index=False
            ).encode("utf-8")

            with col_descarga1:

                st.download_button(
                    "📥 Descargar datos originales",
                    csv_original,
                    file_name=(
                        f"estacion_{codigo_estacion}"
                        f"_datos_originales.csv"
                    ),
                    mime="text/csv",
                    use_container_width=True
                )

            with col_descarga2:

                st.download_button(
                    "✅ Descargar datos sin outliers",
                    csv_limpio,
                    file_name=(
                        f"estacion_{codigo_estacion}"
                        f"_sin_outliers.csv"
                    ),
                    mime="text/csv",
                    use_container_width=True,
                    type="primary"
                )


            # ------------------------------------------------------
            # RESULTADO FINAL
            # ------------------------------------------------------

            st.divider()

            st.success(
                "✅ El DataFrame final `df_sin_outliers` "
                "fue generado correctamente."
            )

            st.write(
                f"""
                **Resultado final de esta consulta**

                - Estación: **{codigo_estacion}**
                - Periodo solicitado: **{fecha_desde} → {fecha_hasta}**
                - Registros originales: **{len(df)}**
                - Outliers eliminados: **{n_outliers}**
                - Registros en `df_sin_outliers`: **{len(df_sin_outliers)}**
                - Huecos originales: **{huecos}**
                - Huecos en `df_sin_outliers`: **{huecos_sin_outliers}**
                """
            )


else:

    st.info(
        "👈 Configura los parámetros en el menú lateral "
        "y presiona **CONSULTAR DATOS** para comenzar."
    )
```
