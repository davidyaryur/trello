import sys
import os
import io
import re
import json
import requests
import http.server
import socketserver
import webbrowser
import mimetypes
from collections import defaultdict

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# ==========================================
# CONFIGURACIÓN GENERAL
# ==========================================
# SE AÑADIÓ EL SCOPE DE GOOGLE DOCS PARA LEER LA ESTRUCTURA DE LOS ENCABEZADOS
SCOPES = [
    'https://www.googleapis.com/auth/drive.readonly',
    'https://www.googleapis.com/auth/documents.readonly' 
]
PORT = 8000
DIRECTORY = os.getcwd()

mimetypes.add_type('application/json; charset=utf-8', '.json')

# ==========================================
# CLASE DEL SERVIDOR HTTP
# ==========================================
class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        self.send_header('Access-Control-Allow-Origin', '*')
        super().end_headers()

# ==========================================
# FASE 1: DESCARGA DESDE GITHUB
# ==========================================
def fase_1_descargar_json_github(año_objetivo):
    print(f"\n--- INICIANDO FASE 1: DESCARGA DE JSON DESDE GITHUB (AÑO {año_objetivo}) ---")
    repo = "davidyaryur/workflows"
    folder_path = "data_trello"
    api_url = f"https://api.github.com/repos/{repo}/contents/{folder_path}"

    try:
        print("Consultando el repositorio de GitHub...")
        response = requests.get(api_url)
        response.raise_for_status()
        archivos = response.json()

        # Filtrar para que solo busque archivos JSON que contengan el año objetivo en su nombre
        archivos_json = [archivo for archivo in archivos if archivo['name'].endswith('.json') and año_objetivo in archivo['name']]

        if not archivos_json:
            print(f"No se encontraron archivos JSON para el año {año_objetivo} en esa ruta.")
            return False

        # Tomamos el último que coincida con ese año
        ultimo_archivo = sorted(archivos_json, key=lambda x: x['name'])[-1]
        url_descarga = ultimo_archivo['download_url']

        print(f"El archivo en GitHub para este año es: {ultimo_archivo['name']}")
        print("Descargando...")

        respuesta_descarga = requests.get(url_descarga)
        respuesta_descarga.raise_for_status()

        ruta_carpeta_actual = os.path.dirname(os.path.abspath(__file__))
        nombre_archivo_nuevo = f"proyectos-inversiones-yar-yur-{año_objetivo}.json"
        ruta_final = os.path.join(ruta_carpeta_actual, nombre_archivo_nuevo)
        
        with open(ruta_final, 'wb') as f:
            f.write(respuesta_descarga.content)

        print(f"¡Éxito! Archivo guardado como '{nombre_archivo_nuevo}' en:\n{ruta_final}")
        return True

    except requests.exceptions.RequestException as e:
        print(f"Hubo un error de conexión con GitHub: {e}")
        return False
    except Exception as e:
        print(f"Ocurrió un error inesperado en la Fase 1: {e}")
        return False

# ==========================================
# OBTENCIÓN DE CREDENCIALES
# ==========================================
def obtener_credenciales():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                os.remove('token.json')
                flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
                creds = flow.run_local_server(port=0)
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    return creds

def extraer_file_id(url):
    match = re.search(r"/d/([a-zA-Z0-9-_]+)", url)
    if match:
        return match.group(1)
    else:
        raise ValueError("No se pudo extraer el ID del archivo. Verifica tu enlace.")

# ==========================================
# FASE 2: DESCARGA DESDE GOOGLE DRIVE (TXT)
# ==========================================
def fase_2_descargar_txt_drive(documentos, creds):
    print("\n--- INICIANDO FASE 2: DESCARGA DE DOCUMENTOS TXT ---")
    try:
        service = build('drive', 'v3', credentials=creds)

        for doc in documentos:
            url_documento = doc["url"]
            nombre_archivo = doc["nombre_archivo"]

            try:
                file_id = extraer_file_id(url_documento)
            except ValueError as e:
                print(e)
                continue

            print(f"Descargando txt de {nombre_archivo}...")
            request = service.files().export_media(fileId=file_id, mimeType='text/plain')
            
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            
            while done is False:
                status, done = downloader.next_chunk()
                    
            with open(nombre_archivo, "wb") as f:
                f.write(fh.getvalue())
                
            print(f"¡Éxito! El documento se guardó como: '{nombre_archivo}'")
        return True

    except Exception as error:
        print(f"Ocurrió un error con la API de Drive: {error}")
        return False

# ==========================================
# FASE 3: PROCESAMIENTO Y GENERACIÓN JSON
# ==========================================
def parse_yar_yur_txt(filename, data=None):
    if data is None:
        data = defaultdict(lambda: defaultdict(list))
    
    month_map = {
        "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
        "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
        "septiembre": "09", "octubre": "10", "noviembre": "11", "diciembre": "12"
    }
    
    allowed_categories = [
        "RENDIMIENTO", "VISUAL", "PLANO MECÁNICO", 
        "MUESTRA", "PRODUCCIÓN", "AUTORIZACIÓN"
    ]
    
    current_date = None
    current_month_key = None
    
    date_pattern = re.compile(r'🗓️\s*\w+\s+(\d+)\s+de\s+(\w+)\s+del\s+(\d+)', re.IGNORECASE)
    project_start_pattern = re.compile(r'^\d+\.\s+([A-ZÁÉÍÓÚÑ\s]+):\s+(\(\d+\))\s+(.*)')
    
    # NUEVA EXPRESIÓN REGULAR PARA CAPTURAR ID DE TRELLO (SOPORTA URLS Y FORMATOS ANTIGUOS)
    trello_pattern = re.compile(r'TRELLO:\s*(?:https?://(?:www\.)?trello\.com/[cb]/)?([A-Za-z0-9]+)(?:/[^\s]*)?')
    
    drive_status_pattern = re.compile(r'DRIVE:\s*(LISTO|FALTA)')
    url_pattern = re.compile(r'(https?://drive\.google\.com\S+)')

    print(f"Leyendo archivo: {filename}...")

    with open(filename, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        
        date_match = date_pattern.search(line)
        if date_match:
            day, month_name, year = date_match.groups()
            month_lower = month_name.lower()
            if month_lower in month_map:
                current_month_key = f"{year}-{month_map[month_lower]} {month_name.upper()} {year}"
                current_date = f"{day.zfill(2)}/{month_map[month_lower]}/{year}"
            i += 1
            continue
            
        proj_match = project_start_pattern.match(line)
        if proj_match and current_date:
            category, pid, content = proj_match.groups()
            category = category.strip()
            
            if category in allowed_categories:
                trello = ""
                drive_link = ""
                check = "FALTA" 
                
                def extract_metadata(text):
                    nonlocal trello, drive_link, check
                    t_match = trello_pattern.search(text)
                    if t_match:
                        trello = t_match.group(1) # Extrae solo el ID sin importar si viene en link o suelto
                        text = text.replace(t_match.group(0), '')
                    
                    d_match = drive_status_pattern.search(text)
                    if d_match:
                        check = d_match.group(1)
                        text = text.replace(d_match.group(0), '')
                        
                    u_match = url_pattern.search(text)
                    if u_match:
                        drive_link = u_match.group(1)
                        text = text.replace(u_match.group(0), '')
                    return text.strip()

                content = extract_metadata(content)
                description_buffer = []
                
                parts = content.split(' - ', 2)
                if len(parts) >= 3:
                    numero_proyecto = parts[0].strip()
                    cliente = parts[1].strip()
                    rest = parts[2].strip()
                    
                    words = rest.split()
                    found_desc = False
                    title_part = rest
                    desc_part = ""

                    for word in words:
                        if any(c.islower() for c in word):
                            match_idx = rest.find(word)
                            if match_idx != -1:
                                title_part = rest[:match_idx].strip()
                                desc_part = rest[match_idx:].strip()
                                found_desc = True
                            break
                    
                    if not found_desc: 
                        title_part = rest
                        desc_part = ""
                        
                    description_buffer.append(desc_part)
                else:
                    numero_proyecto = "S/N"
                    cliente = "DESCONOCIDO"
                    title_part = content
                
                j = i + 1
                while j < len(lines):
                    next_line = lines[j].strip()
                    if (date_pattern.search(next_line) or 
                        project_start_pattern.match(next_line) or 
                        "📝SALIDA" in next_line or 
                        "📂 CARPETAS" in next_line):
                        break
                    
                    if next_line:
                        old_line = next_line
                        next_line = extract_metadata(next_line)
                        if next_line and next_line != old_line:
                             pass 
                        elif next_line:
                             description_buffer.append(next_line)
                    j += 1
                
                i = j - 1
                full_description = " ".join(filter(None, description_buffer)).strip()
                
                project_obj = {
                    "id": pid,
                    "fecha": current_date,
                    "trabajoDiseno": category,
                    "numeroProyecto": numero_proyecto,
                    "cliente": cliente,
                    "tituloProyecto": title_part,
                    "trello": trello,
                    "descripcion": full_description,
                    "drive": drive_link,
                    "check": check
                }
                
                data[current_month_key][category].append(project_obj)
        i += 1
    return data

def fase_3_procesar_y_generar_json(archivos_txt):
    print("\n--- INICIANDO FASE 3: PROCESAMIENTO Y GENERACIÓN DE JSON ---")
    master_data = defaultdict(lambda: defaultdict(list))
    files_found = 0
    
    for input_file in archivos_txt:
        if os.path.exists(input_file):
            parse_yar_yur_txt(input_file, master_data)
            files_found += 1
            
    if files_found > 0:
        allowed_categories = ["RENDIMIENTO", "VISUAL", "PLANO MECÁNICO", "MUESTRA", "PRODUCCIÓN", "AUTORIZACIÓN"]
        table_id_counter = 1
        sorted_months = sorted(master_data.keys()) 

        for mes_key in sorted_months:
            month_data = master_data[mes_key]
            json_output = []
            mes_name = mes_key.split(' ')[1]
            
            for cat in allowed_categories:
                proyectos = month_data.get(cat, [])
                if proyectos:
                    proyectos.sort(key=lambda x: (x['fecha'], x['id']))
                    table_obj = {
                        "tableId": table_id_counter,
                        "mes": mes_name,
                        "num": table_id_counter,
                        "proyectos": proyectos
                    }
                    json_output.append(table_obj)
                    table_id_counter += 1
            
            filename = f"{mes_key}.json"
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(json_output, f, ensure_ascii=False, indent=2)
            print(f" -> Generado JSON: {filename}")
        return True
    return False

# ==========================================
# FASE 4: EXTRACCIÓN DE ENLACES A JSON
# ==========================================
def fase_4_generar_json_enlaces(documentos, creds):
    print("\n--- INICIANDO FASE 4: EXTRACCIÓN DE ENLACES DE ENCABEZADOS Y GENERACIÓN DE JSON ---")
    token = creds.token
    headers = {'Authorization': f'Bearer {token}'}

    meses_validos = ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", 
                     "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"]
    month_map = {
        "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
        "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
        "septiembre": "09", "octubre": "10", "noviembre": "11", "diciembre": "12"
    }
    date_heading_regex = re.compile(r'(\d{1,2})\s+de\s+([a-zA-ZáéíóúÁÉÍÓÚñÑ]+)\s+del?\s+(\d{4})', re.IGNORECASE)

    for doc in documentos:
        try:
            file_id = extraer_file_id(doc["url"])
            print(f"Analizando estructura interna de {doc['nombre_archivo']}...")

            # Llamada directa a la API REST de Google Docs para obtener estructura con Tabs
            url_api = f"https://docs.googleapis.com/v1/documents/{file_id}?includeTabsContent=true"
            response = requests.get(url_api, headers=headers)
            response.raise_for_status()

            doc_json = response.json()
            tabs = doc_json.get('tabs', [])

            if not tabs:
                print(f"El documento {doc['nombre_archivo']} no tiene la estructura de pestañas.")
                continue

            año = "2025" if "2025" in doc['nombre_archivo'] else "2026"
            enlaces_fechas = {}

            for tab in tabs:
                tab_props = tab.get('tabProperties', {})
                tab_id = tab_props.get('tabId')
                tab_title = tab_props.get('title', 'Sin_Titulo').strip().upper()

                # Solo procesar pestañas que correspondan a meses
                if not any(mes in tab_title for mes in meses_validos):
                    continue

                content = tab.get('documentTab', {}).get('body', {}).get('content', [])

                for element in content:
                    paragraph = element.get('paragraph')
                    if paragraph:
                        style = paragraph.get('paragraphStyle', {})
                        heading_id = style.get('headingId')

                        if heading_id:
                            text_content = ""
                            for elem in paragraph.get('elements', []):
                                text_run = elem.get('textRun')
                                if text_run:
                                    text_content += text_run.get('content', '')
                            
                            text_content = text_content.strip()
                            if not text_content:
                                continue

                            # Extraer fecha si el encabezado corresponde a un día
                            date_match = date_heading_regex.search(text_content)
                            if date_match:
                                d_str, m_str, y_str = date_match.groups()
                                m_lower = m_str.lower()
                                if m_lower in month_map:
                                    enlace = f"https://docs.google.com/document/d/{file_id}/edit?tab={tab_id}#heading={heading_id}"
                                    fecha_clave = f"{d_str.zfill(2)}/{month_map[m_lower]}/{y_str}"
                                    enlaces_fechas[fecha_clave] = enlace

            # Guardar el JSON consolidado de enlaces de fechas para este año
            if enlaces_fechas:
                nombre_json_enlaces = f"enlaces_fechas_{año}.json"
                with open(nombre_json_enlaces, 'w', encoding='utf-8') as f:
                    json.dump(enlaces_fechas, f, ensure_ascii=False, indent=2)
                print(f" -> JSON de Enlaces Generado: {nombre_json_enlaces} ({len(enlaces_fechas)} fechas registradas)")

        except Exception as e:
            print(f"Error procesando los encabezados de {doc['nombre_archivo']}: {e}")

# ==========================================
# INICIO DEL SERVIDOR
# ==========================================
def iniciar_servidor():
    print("\n--- INICIANDO SERVIDOR WEB ---")
    try:
        with socketserver.TCPServer(("", PORT), Handler) as httpd:
            url = f"http://localhost:{PORT}"
            print(f"\n🚀 Servidor Inv. Yar-Yur iniciado en: {url}")
            print(f"📂 Sirviendo archivos desde: {DIRECTORY}")
            print("\nPresiona Ctrl+C para detener el servidor.")
            webbrowser.open(url)
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 Servidor detenido.")
    except OSError as e:
        if e.errno == 98 or e.errno == 10048: 
            print(f"\n⚠️ El puerto {PORT} está ocupado. Intenta cerrar otras instancias de Python.")
        else:
            raise

# ==========================================
# EJECUCIÓN PRINCIPAL (ORQUESTADOR)
# ==========================================
def main():
    # Extraer el nombre del script actual (ej. "2025.py")
    nombre_script = os.path.basename(sys.argv[0])
    
    # Buscar un año en el nombre del archivo usando expresiones regulares
    match_año = re.search(r'(20\d{2})', nombre_script)
    
    if not match_año:
        print("❌ No se detectó un año válido (ej. 2025, 2026) en el nombre del archivo.")
        print(f"El archivo actual se llama: {nombre_script}")
        print("Por favor, renombra el archivo a algo como '2025.py' o 'actualizador_2026.py'")
        return
        
    año_objetivo = match_año.group(1)
    print(f"\n=========================================")
    print(f"               AÑO {año_objetivo} ")
    print(f"=========================================\n")

    # BUCLE DE PREGUNTA Y/N
    while True:
        respuesta = input("¿Deseas actualizar los datos? (Y/N): ").strip().upper()
        if respuesta in ['Y', 'N']:
            break
        print("Por favor, ingresa 'Y' para actualizar o 'N' para iniciar solo el servidor.")

    # SI LA RESPUESTA ES 'N', SALTAMOS DIRECTO AL SERVIDOR
    if respuesta == 'N':
        print("\nSaltando proceso de actualización. Iniciando el servidor con los archivos locales...")
        iniciar_servidor()
        return

    # SI LA RESPUESTA ES 'Y', CONTINUAMOS CON EL FLUJO NORMAL
    # Tu lista general de todos los documentos posibles
    todos_los_documentos = [
        {
            "url": "https://docs.google.com/document/d/1ZfeOYzi1ihtYkW5z9yl_lE0IITFMwQ9lMP9_sQgP2co",
            "nombre_archivo": "Yar-Yur 2026.txt"
        },
        {
            "url": "https://docs.google.com/document/d/1KZ37s_uI1POU_ktcNdWkBf7NFGqNps9KeCF3lHRk5q0",
            "nombre_archivo": "Yar-Yur 2025.txt"
        }
    ]
    
    # Filtrar solo el documento que coincide con el año objetivo
    documentos = [doc for doc in todos_los_documentos if año_objetivo in doc["nombre_archivo"]]
    archivos_txt = [doc["nombre_archivo"] for doc in documentos]

    if not documentos:
        print(f"❌ No se encontró configuración de URL para los documentos del año {año_objetivo}.")
        return

    # Fase 1: Ahora le pasamos el año objetivo
    fase_1_descargar_json_github(año_objetivo)
    
    # Obtener credenciales
    creds = obtener_credenciales()
    if not creds:
        print("❌ Error de autenticación.")
        return

    # Fase 2: Solo procesará el documento filtrado
    exito_fase_2 = fase_2_descargar_txt_drive(documentos, creds)
    if not exito_fase_2:
        return

    # Fase 3: Solo procesará el .txt filtrado
    exito_fase_3 = fase_3_procesar_y_generar_json(archivos_txt)
    
    # Fase 4: Solo generará los json de enlaces para los documentos de ese año
    fase_4_generar_json_enlaces(documentos, creds)
    
    # Lanzar servidor
    if exito_fase_3:
        print(f"\n🚀 ¡FLUJO DEL AÑO {año_objetivo} COMPLETADO CORRECTAMENTE!")
        iniciar_servidor()

if __name__ == '__main__':
    main()