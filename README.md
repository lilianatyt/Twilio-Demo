# Demo de Twilio ConversationRelay: Tito de Tacos y Tortas TYT

Agente telefónico en español (es-MX) hecho con Twilio ConversationRelay, FastAPI y un LLM compatible con OpenAI.

- `main_bd.py`: el agente. Atiende la llamada y consulta, crea, modifica y cancela pedidos.
- `pedidos_api.py`: la API de pedidos, con SQLite.

## Requisitos

- Python 3.11 o mayor
- [ngrok](https://ngrok.com) con tu authtoken configurado
- Una cuenta de Twilio con un número con voz y el AI Features Addendum aceptado
- Una API key de [LLM API](https://docs.llmapi.ai/)

## Instalación

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Copia `.env.example` como `.env` y llena tus valores. `DOMAIN` es tu dominio de ngrok, sin `https://`.

## Base de datos

No hay que instalar nada. La primera vez que corre `pedidos_api.py` se crea el archivo `pedidos.db` con los pedidos de prueba 1001 a 4004. Para empezar de cero, borra `pedidos.db`.

## Cómo correrlo

Cada comando va en su propia terminal. Las de uvicorn necesitan el `.venv` activado.

```powershell
ngrok http 8000
uvicorn pedidos_api:app --port 8001 --reload
uvicorn main_bd:app --host 0.0.0.0 --port 8000 --reload
```

## Configurar Twilio

En la consola, abre tu número y en la configuración de voz pon:

- A call comes in: **Webhook**
- URL: `https://TU_DOMINIO/twiml`
- Método: **HTTP POST**

Después llama al número.

## Probar otras configuraciones

En `main_bd.py`, cambia la línea `PERFIL = "nova_medio"` por otro perfil de `PERFILES` y guarda. Cada perfil tiene un comentario que explica qué cambia.
