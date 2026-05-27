# DiscograPY — Creador de Playlists de Discografía en Spotify

[![Python Version](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> [English version](README.md)

DiscograPY crea playlists de discografía en Spotify. Incluye:

- Una **aplicación web** (Flask + HTML/CSS/JS vanilla) — busca artistas, configura filtros y obtiene una playlist con preview integrado.
- Una **CLI** (`playlists.py`) — misma lógica central, flujo interactivo en terminal.

Construido sobre la [Spotify Web API](https://developer.spotify.com/documentation/web-api/) mediante [Spotipy](https://spotipy.readthedocs.io/). Metadatos de artistas (géneros, país) enriquecidos desde [MusicBrainz](https://musicbrainz.org/).

## App en producción

**[discograpy-production.up.railway.app](https://discograpy-production.up.railway.app/)**

---

## Tabla de contenidos

- [Características](#características)
- [Requisitos](#requisitos)
- [Instalación](#instalación)
- [Configuración](#configuración)
- [Obtener un Refresh Token](#obtener-un-refresh-token)
- [Uso — Web](#uso--web)
- [Uso — CLI](#uso--cli)
- [Filtros de contenido](#filtros-de-contenido)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Logging](#logging)
- [Despliegue](#despliegue)
- [Notas y limitaciones](#notas-y-limitaciones)
- [Solución de problemas](#solución-de-problemas)
- [Licencia](#licencia)

---

## Características

- Busca cualquier artista; los resultados muestran seguidores, géneros y país (enriquecido via MusicBrainz).
- **7 modos de tipo de álbum:** Todo, Solo LPs, Solo EPs, Solo Singles, Solo Compilaciones, EPs + Singles, LPs + EPs + Singles.
- Detección de EP por conteo de pistas (4–7 pistas = EP, 1–3 = Single), ya que Spotify reporta ambos como `single`.
- Sufijo de playlist ajustado automáticamente cuando los tipos pedidos no existen para un artista.
- **Filtros de contenido** (web): excluir o incluir versiones en vivo, demos, remixes e instrumentales por pedido.
- **Deduplicación inteligente** (activa por defecto): conserva la mejor versión de cada pista por título normalizado y prioridad de lanzamiento; elimina duplicados regionales y redundancias de ediciones deluxe.
- Descarga paralela de pistas de álbumes (`ThreadPoolExecutor`, hasta 8 workers).
- Pistas agregadas en orden cronológico de lanzamiento.
- **Imagen del artista como portada** (requiere scope `ugc-image-upload`).
- **Modo dry-run** (CLI): descubrimiento y filtrado completo sin crear la playlist.
- Reintentos con backoff exponencial y soporte del header `Retry-After` para HTTP 429.
- Logging UTF-8 a archivo y consola; compatible con nombres de artistas en caracteres no latinos.

---

## Requisitos

- Python 3.8+
- Cuenta de Spotify Developer con credenciales de app
- Paquetes Python: `flask`, `gunicorn`, `spotipy`, `python-dotenv`, `flask-cors`, `requests`, `pycountry`

---

## Instalación

```bash
pip install -r requirements.txt
```

---

## Configuración

### 1. Crear una app en Spotify Developer

1. Ve a [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard).
2. Clic en **Create an App**, completa nombre y descripción.
3. Tras la creación, anota tu **Client ID** y **Client Secret**.

### 2. Agregar URIs de redirección

En configuración de la app → **Edit Settings** → **Redirect URIs**, agrega:

Para desarrollo local:

```text
http://127.0.0.1:5000/callback
```

Para producción (Railway):

```text
https://discograpy-production.up.railway.app/callback
```

Clic en **Add** y luego **Save** para cada una.

### 3. Configurar variables de entorno

Crea un archivo `.env` en la raíz del proyecto:

```env
SPOTIPY_CLIENT_ID=tu_client_id_aqui
SPOTIPY_CLIENT_SECRET=tu_client_secret_aqui
SPOTIPY_REDIRECT_URI=http://127.0.0.1:5000/callback
SPOTIPY_REFRESH_TOKEN=tu_refresh_token_aqui

# Opcional
FLASK_SECRET_KEY=cambia-esto-en-produccion
SPOTIPY_USE_CACHE=false
```

`SPOTIPY_REFRESH_TOKEN` — requerido para la app web (creación de playlists en el servidor). Obtenlo con `get_token.py` (ver abajo). Sin él, cada reinicio del servidor requiere re-autenticación.

`SPOTIPY_USE_CACHE` — pon `true` para cachear el token OAuth en `.spotify_cache` durante desarrollo local. Mantén `false` en producción.

> Nunca subas `.env` al repositorio — está listado en `.gitignore`.

---

## Obtener un Refresh Token

Ejecuta esto una vez localmente para autenticarte e imprimir tu refresh token:

```bash
python get_token.py
```

Se abrirá una ventana del navegador para OAuth de Spotify. Tras autorizar, la terminal imprime:

```text
REFRESH TOKEN: AQA...
```

Copia ese valor en `SPOTIPY_REFRESH_TOKEN` en tu `.env`.

Scopes otorgados: `playlist-modify-public playlist-modify-private ugc-image-upload`.

---

## Uso — Web

Iniciar localmente:

```bash
python app.py
```

Abre `http://127.0.0.1:5000`.

Ejecución local estilo producción:

```bash
gunicorn app:app --bind 0.0.0.0:5000
```

### Flujo

1. **Buscar** — escribe el nombre de un artista; los resultados muestran nombre, seguidores, géneros y país.
2. **Configurar** — selecciona tipo de álbum, activa filtros de contenido (en vivo, demos, remixes, instrumentales, versiones duplicadas), y opcionalmente usa la imagen del artista como portada.
3. **Resultado** — preview integrado de Spotify tras la creación.

---

## Uso — CLI

```bash
python playlists.py [--verbose] [--dry-run]
```

| Flag | Efecto |
|---|---|
| `-v` / `--verbose` | Logging DEBUG en consola |
| `--dry-run` | Descubre y filtra pistas; no crea la playlist |

### Flujo interactivo

1. Ingresa el nombre del artista.
2. Selecciona de los resultados (muestra seguidores y géneros).
3. Elige el tipo de álbum (0–6).
4. La playlist es creada y se imprime la URL.

**Nota:** La CLI usa la configuración de filtros por defecto — versiones en vivo, demos, remixes e instrumentales son excluidos; deduplicación inteligente aplicada. Para cambiar filtros, usa la interfaz web.

### Opciones de tipo de álbum

| # | Etiqueta | Qué incluye |
|---|---|---|
| 0 | Todo | Todos los tipos combinados |
| 1 | Solo LPs | Álbumes de larga duración |
| 2 | Solo EPs | Lanzamientos tipo single con 4–7 pistas |
| 3 | Solo Singles | Lanzamientos tipo single con 1–3 pistas |
| 4 | Solo Compilaciones | Compilaciones |
| 5 | EPs + Singles | EPs y Singles, sin LPs ni Compilaciones |
| 6 | LPs + EPs + Singles | Todo excepto Compilaciones |

---

## Filtros de contenido

Disponibles en la interfaz web (enviados como booleanos a `POST /api/create`):

| Filtro | Por defecto | Efecto al activar |
|---|---|---|
| `include_live_versions` | desactivado | Incluye pistas/álbumes con "live", "en vivo", etc. en el nombre |
| `include_demos` | desactivado | Incluye pistas/álbumes con "demo", "rough mix", etc. |
| `include_remixes` | desactivado | Incluye remixes, reworks, edits, extended mixes |
| `include_instrumentals` | desactivado | Incluye versiones instrumentales (solo si existe el original) |
| `include_duplicate_versions` | desactivado | Desactiva deduplicación; conserva todas las versiones de cada pista |
| `use_artist_image_as_cover` | desactivado | Sube la imagen de Spotify del artista como portada de la playlist |

Cuando la deduplicación está activa (por defecto), los duplicados se resuelven por comparación de título normalizado — contenido entre corchetes y palabras clave filtradas — conservando la versión del álbum con mayor prioridad de lanzamiento (LP > EP > Single).

---

## Estructura del proyecto

```text
discograpy/
├── app.py                    # Punto de entrada Flask
├── playlists.py              # Punto de entrada CLI
├── get_token.py              # Helper local: obtener refresh token
├── src/
│   ├── config.py             # Factory de cliente Spotify, validación de env vars
│   ├── logging_config.py     # Configuración de logging (archivo + consola, UTF-8)
│   ├── domain/
│   │   ├── album_types.py    # Config de tipos, matching, lógica de sufijos
│   │   ├── filters.py        # Filtros de contenido y deduplicación de pistas
│   │   └── models.py         # Dataclass RunSummary
│   ├── services/
│   │   ├── discography.py    # DiscographyService: orquesta el flujo completo
│   │   ├── spotify_client.py # SpotifyClient: wrapper de Spotipy + reintentos
│   │   ├── musicbrainz.py    # Enriquecimiento de metadatos MusicBrainz (géneros, país)
│   │   └── retry.py          # Decorador retry_on_failure con backoff
│   ├── web/
│   │   ├── __init__.py       # Factory de app Flask, singletons cliente/servicio
│   │   └── routes.py         # Rutas HTTP y endpoints de API
│   └── cli/
│       ├── runner.py         # Lógica de orquestación CLI
│       └── ui.py             # Spinner, menús, display de artistas/resumen
├── templates/
│   └── index.html            # Frontend de página única
├── requirements.txt
├── Procfile                  # Definición de proceso Railway/Heroku
├── railway.toml              # Config de despliegue Railway
└── README.es.md
```

---

## Logging

Formato de log:

```text
%(asctime)s - %(name)s - %(levelname)s - %(message)s
```

| Salida | Nivel |
|---|---|
| Consola | `INFO` (o `DEBUG` con `--verbose`) |
| `spotify_discography.log` | `DEBUG` siempre |

El stream de consola se reconfigura a UTF-8 con `errors='replace'` para compatibilidad con Windows.

---

## Despliegue

Desplegado en [Railway](https://railway.app/) usando Nixpacks.

Comando de inicio: `gunicorn app:app --bind 0.0.0.0:$PORT`

Health check path: `/`

Política de reinicio: `on_failure`

Variables de entorno requeridas en Railway: `SPOTIPY_CLIENT_ID`, `SPOTIPY_CLIENT_SECRET`, `SPOTIPY_REDIRECT_URI`, `SPOTIPY_REFRESH_TOKEN`, `FLASK_SECRET_KEY`.

---

## Notas y limitaciones

- **Tamaño de batch:** Límite de la API de Spotify de 100 pistas por petición de agregado. Manejado automáticamente con batching paralelo (hasta 4 uploads concurrentes).
- **Duplicados regionales:** Spotify devuelve versiones específicas de mercado de álbumes por separado. La deduplicación reduce esto, pero activar `include_duplicate_versions` las incluirá todas.
- **Las playlists son públicas por defecto.** Para crear playlists privadas, cambia `public=True` por `public=False` en `src/services/spotify_client.py` dentro de `create_playlist`.
- **Subida de portada** requiere el scope `ugc-image-upload` en el refresh token. Si el token se obtuvo sin él, vuelve a ejecutar `get_token.py`.
- **MusicBrainz** tiene timeout de 1.8s y caché LRU por proceso. Los fallos de enriquecimiento no son fatales; los metadatos faltantes se reemplazan con los géneros propios de Spotify.

---

## Solución de problemas

<details>
<summary>Error de autenticación / credenciales inválidas</summary>

1. Verifica que `.env` tenga `SPOTIPY_CLIENT_ID` y `SPOTIPY_CLIENT_SECRET` correctos.
2. Confirma que el redirect URI en `.env` coincide exactamente con el configurado en el Spotify Developer Dashboard.
3. Vuelve a ejecutar `get_token.py` para obtener un `SPOTIPY_REFRESH_TOKEN` fresco.

</details>

<details>
<summary>Rate limiting (HTTP 429)</summary>

El decorador de reintentos lee el header `Retry-After` y espera el tiempo indicado con jitter adicional. Rate limiting persistente indica que la app de Spotify está siendo throttled. Espera unos minutos y reintenta.

</details>

<details>
<summary>No se encontró contenido para el tipo seleccionado</summary>

1. Verifica que el artista realmente tenga ese tipo de lanzamiento en Spotify.
2. Prueba la opción `0` (Todo) para ver todo el contenido disponible.
3. La app avisa y ajusta el nombre de la playlist cuando un tipo pedido está ausente.

</details>

<details>
<summary>Subida de portada rechazada (401)</summary>

El `SPOTIPY_REFRESH_TOKEN` fue generado sin el scope `ugc-image-upload`. Vuelve a ejecutar `get_token.py` y actualiza el token en tu entorno.

</details>

<details>
<summary>Errores de importación (ModuleNotFoundError)</summary>

```bash
pip install -r requirements.txt
python --version  # debe ser 3.8+
```

</details>

---

## Licencia

Licencia MIT. Ver [LICENSE](LICENSE).

---

## Autor

Desarrollado para amantes de la automatización y la música.

- GitHub: [@based-on-what](https://github.com/based-on-what)
- Proyecto: [github.com/based-on-what/discograpy](https://github.com/based-on-what/discograpy)

Agradecimientos: [Spotipy](https://spotipy.readthedocs.io/), [Spotify Web API](https://developer.spotify.com/documentation/web-api/), [MusicBrainz](https://musicbrainz.org/).
