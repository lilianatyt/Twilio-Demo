import json
import os
import time

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from openai import AsyncOpenAI
from twilio.twiml.voice_response import Connect, VoiceResponse

load_dotenv()
app = FastAPI()
client = AsyncOpenAI(
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL"),
)
DOMAIN = os.getenv("DOMAIN")
MODELO = "gpt-6-luna"

PEDIDOS_API_URL = os.getenv("PEDIDOS_API_URL", "http://localhost:8001")
http = httpx.AsyncClient(base_url=PEDIDOS_API_URL, timeout=5.0)

# Cambia esta línea para probar otra configuración. Al guardar, uvicorn se reinicia solo.
PERFIL = "nova_medio"

PERFILES = {
    # Valores por defecto de Twilio: responde rápido y cualquier sonido lo interrumpe
    "default": {
        "speechModel": "nova-3-general",
        "interruptSensitivity": "high",
        "ignoreBackchannel": "false",
    },
    # Espera 1 s de silencio antes de responder, sensibilidad media, ignora "ajá"
    "nova_medio": {
        "speechModel": "nova-3-general",
        "speechTimeout": "1000",
        "interruptSensitivity": "medium",
        "ignoreBackchannel": "true",
    },
    # Para ruido: espera 1.5 s, solo lo interrumpe una frase clara, filtra su propio eco
    "nova_ruido": {
        "speechModel": "nova-3-general",
        "speechTimeout": "1500",
        "interruptSensitivity": "low",
        "ignoreBackchannel": "true",
        "echoPromptFiltering": "true",
    },
    # Deepgram Flux: decide que terminaste por lo que dices, no solo por el silencio
    "flux": {
        "speechModel": "flux",
        "eotThreshold": "0.8",
        "speechTimeout": "3000",
        "interruptSensitivity": "medium",
        "ignoreBackchannel": "true",
    },
    # Flux para ruido: solo lo interrumpe una frase clara, filtra su propio eco
    "flux_ruido": {
        "speechModel": "flux",
        "eotThreshold": "0.75",
        "speechTimeout": "3000",
        "interruptSensitivity": "low",
        "ignoreBackchannel": "true",
        "echoPromptFiltering": "true",
    },
}


@app.post("/twiml")
async def twiml_endpoint():
    response = VoiceResponse()
    connect = Connect()
    connect.conversation_relay(
        url=f"wss://{DOMAIN}/ws",
        language="es-MX",
        welcomeGreeting="Hola, gracias por llamar a Tacos y Tortas TYT. Soy Tito, ¿en qué le puedo ayudar?",
        ttsProvider="ElevenLabs",
        voice="nmvA11Y688M5reLqDsVm-v4",
        transcriptionProvider="Deepgram",
        hints="TYT,Tacos y Tortas TYT,trompo,suadero,bistec,milanesa,limonada,jamaica",
        interruptible="any",
        preemptible="true",
        **PERFILES[PERFIL],
    )
    response.append(connect)
    response.say(
        "Gracias por llamar a Tacos y Tortas TYT. Que tenga buen día.",
        voice="ElevenLabs.nmvA11Y688M5reLqDsVm",
        language="es-MX",
    )
    response.hangup()
    print(f"[twiml] perfil={PERFIL}")
    return Response(content=str(response), media_type="text/xml")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    call_sid = None
    messages: list[dict] = []
    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            msg_type = data.get("type")
            if msg_type == "setup":
                call_sid = data.get("callSid")
                print(f"[{call_sid}] Call connected")
            elif msg_type == "prompt" and data.get("last"):
                user_text = data["voicePrompt"]
                print(f"[{call_sid}] Caller: {user_text}")
                messages.append({"role": "user", "content": user_text})
                response_text = await stream_response(websocket, call_sid, messages)
                messages.append({"role": "assistant", "content": response_text})
            elif msg_type == "interrupt":
                spoken = data.get("utteranceUntilInterrupt", "")
                print(f"[{call_sid}] Interrupted after: '{spoken}'")
                # Se guarda solo lo que el cliente alcanzó a escuchar
                if messages and messages[-1]["role"] == "assistant":
                    if spoken:
                        messages[-1]["content"] = spoken
                    else:
                        messages.pop()
            elif msg_type == "error":
                print(f"[{call_sid}] Conversation Relay error: {data.get('description')}")
    except WebSocketDisconnect:
        print(f"[{call_sid}] Call ended")


SYSTEM_PROMPT = """Eres Tito, el asistente telefónico virtual de Tacos y Tortas TYT, una taquería con servicio a domicilio.
Ayudas a los clientes con:
- Estatus de un pedido (usa consultar_estatus_pedido con el número de pedido)
- Pedidos nuevos (pide el nombre del cliente, la dirección de entrega y lo que quiere ordenar; usa crear_pedido)
- Cambios de dirección o de productos en un pedido que todavía no sale (usa modificar_pedido)
- Cancelaciones de un pedido que todavía no sale (usa cancelar_pedido)
- Menú (tacos de trompo, suadero o bistec a veinte pesos cada uno; tortas de milanesa o de trompo a sesenta y cinco pesos; agua de jamaica o limonada a treinta pesos)
- Costo de envío (gratis en pedidos de doscientos pesos o más; en pedidos menores cuesta treinta pesos)
- Tiempo de entrega (entre treinta y cuarenta y cinco minutos)
- Horario (todos los días de una de la tarde a once de la noche)
- Formas de pago (efectivo o tarjeta al momento de la entrega)
Habla de forma natural, como en una llamada telefónica. Sigue estas reglas:
- Responde siempre en español de México y trata al cliente de usted
- Usa solo oraciones simples. Sin viñetas, sin markdown, sin emojis
- Escribe todos los números con letra ("veinte pesos", no "$20")
- Responde con dos o tres oraciones como máximo
- Cuando el cliente te dé un nombre, una dirección, un número de pedido o una cantidad, repítelo para confirmarlo
- Antes de crear, modificar o cancelar un pedido, repite los datos y pregunta si son correctos. Usa la herramienta solo cuando el cliente responda que sí. Si dice que no, pregunta qué hay que corregir
- No calcules el total tú. Después de crear o modificar un pedido, di el número de pedido dígito por dígito y el total que regresa el sistema
- Si no puedes ayudar con algo, dilo brevemente y ofrece comunicarlo con un encargado
- Nunca inventes información que no esté en esta lista
- Si después de uno o dos intercambios el cliente no ha dicho qué necesita, ofrécele brevemente las opciones: consultar un pedido, hacer un pedido nuevo, o cambiar o cancelar un pedido
- Cuando el cliente diga que es todo o se despida, pregúntale si le puedes ayudar en algo más. Si responde que no, usa terminar_llamada sin decir nada más"""

PRODUCTOS_MENU = [
    "taco de trompo", "taco de suadero", "taco de bistec",
    "torta de milanesa", "torta de trompo",
    "limonada", "agua de jamaica",
]

NUMERO_PEDIDO = {
    "type": "string",
    "description": "El número de pedido de cuatro dígitos, por ejemplo 1001. Solo dígitos, sin espacios.",
}

LISTA_PRODUCTOS = {
    "type": "array",
    "description": "Lista completa de productos del pedido.",
    "items": {
        "type": "object",
        "properties": {
            "producto": {"type": "string", "enum": PRODUCTOS_MENU},
            "cantidad": {"type": "integer", "minimum": 1},
        },
        "required": ["producto", "cantidad"],
    },
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "consultar_estatus_pedido",
            "description": "Consulta el estatus actual de un pedido con su número de pedido.",
            "parameters": {
                "type": "object",
                "properties": {"numero_pedido": NUMERO_PEDIDO},
                "required": ["numero_pedido"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "crear_pedido",
            "description": "Registra un pedido nuevo. Úsala solo después de repetirle al cliente su nombre, dirección y productos, y de que él confirme que son correctos.",
            "parameters": {
                "type": "object",
                "properties": {
                    "nombre": {"type": "string", "description": "Nombre del cliente."},
                    "direccion": {"type": "string", "description": "Dirección de entrega completa."},
                    "productos": LISTA_PRODUCTOS,
                },
                "required": ["nombre", "direccion", "productos"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "modificar_pedido",
            "description": "Cambia la dirección o los productos de un pedido que todavía no sale. Úsala solo después de repetirle al cliente el cambio y de que él confirme. Si cambian los productos, manda la lista completa como debe quedar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "numero_pedido": NUMERO_PEDIDO,
                    "direccion": {"type": "string", "description": "Nueva dirección de entrega, solo si cambia."},
                    "productos": LISTA_PRODUCTOS,
                },
                "required": ["numero_pedido"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancelar_pedido",
            "description": "Cancela un pedido que todavía no sale. Úsala solo después de preguntarle al cliente si está seguro y de que él confirme.",
            "parameters": {
                "type": "object",
                "properties": {"numero_pedido": NUMERO_PEDIDO},
                "required": ["numero_pedido"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "terminar_llamada",
            "description": "Termina la llamada. Úsala solo cuando el cliente confirme que ya no necesita nada más",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def solo_digitos(texto: str) -> str:
    return "".join(c for c in texto if c.isdigit())


async def execute_tool(name: str, args: dict) -> str:
    numero = solo_digitos(args.get("numero_pedido", ""))
    try:
        if name == "consultar_estatus_pedido":
            r = await http.get(f"/pedidos/{numero}")
        elif name == "crear_pedido":
            r = await http.post("/pedidos", json=args)
        elif name == "modificar_pedido":
            cambios = {k: v for k, v in args.items() if k != "numero_pedido"}
            r = await http.patch(f"/pedidos/{numero}", json=cambios)
        elif name == "cancelar_pedido":
            r = await http.post(f"/pedidos/{numero}/cancelar")
        else:
            return "Herramienta desconocida."
    except httpx.HTTPError:
        return "No se pudo conectar con el sistema de pedidos."
    return r.text


async def stream_response(
    websocket: WebSocket,
    call_sid: str | None,
    messages: list[dict],
) -> str:
    full_response = ""
    terminada = False
    openai_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages
    try:
        stream = await client.chat.completions.create(
            model=MODELO,
            reasoning_effort="none",
            max_completion_tokens=300,
            messages=openai_messages,
            tools=TOOLS,
            stream=True,
        )
        finish_reason = None
        tool_calls_acc: dict[int, dict] = {}
        async for chunk in stream:
            # El gateway manda al final un chunk sin choices
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            finish_reason = choice.finish_reason or finish_reason
            if delta.content:
                full_response += delta.content
                await websocket.send_text(
                    json.dumps({"type": "text", "token": delta.content, "last": False})
                )
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    entry = tool_calls_acc.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                    if tc.id:
                        entry["id"] = tc.id
                    if tc.function and tc.function.name:
                        entry["name"] = tc.function.name
                    if tc.function and tc.function.arguments:
                        entry["arguments"] += tc.function.arguments

        if finish_reason == "tool_calls" and tool_calls_acc:
            tool_calls = list(tool_calls_acc.values())
            if any(tc["name"] == "terminar_llamada" for tc in tool_calls):
                print(f"[{call_sid}] Tito terminó la llamada")
                await websocket.send_text(json.dumps({
                    "type": "end",
                    "handoffData": json.dumps({"reasonCode": "fin-de-llamada"}),
                }))
                terminada = True
                return "(llamada terminada)"

            openai_messages.append({
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["arguments"]},
                    }
                    for tc in tool_calls
                ],
            })
            for tc in tool_calls:
                args = json.loads(tc["arguments"] or "{}")
                inicio = time.perf_counter()
                result = await execute_tool(tc["name"], args)
                ms = (time.perf_counter() - inicio) * 1000
                print(f"[{call_sid}] Tool {tc['name']}({args}) -> {result} ({ms:.0f} ms)")
                openai_messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                })
            full_response = ""
            stream2 = await client.chat.completions.create(
                model=MODELO,
                reasoning_effort="none",
                max_completion_tokens=400,
                messages=openai_messages,
                stream=True,
            )
            async for chunk in stream2:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    full_response += delta.content
                    await websocket.send_text(
                        json.dumps({"type": "text", "token": delta.content, "last": False})
                    )
    finally:
        # Después de "end" la sesión ya está cerrada
        if not terminada:
            await websocket.send_text(json.dumps({"type": "text", "token": "", "last": True}))
    print(f"[{call_sid}] Tito: {full_response}")
    return full_response
