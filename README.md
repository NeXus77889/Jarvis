# J.A.R.V.I.S. — Fases 1–5 (implementación incremental)

Asistente personal híbrido: el equipo local gestiona sesiones, memoria SQLite y comandos locales; Groq proporciona el razonamiento en la nube cuando existe una clave configurada. La memoria y las funciones locales no requieren GPU ni VRAM.

## Requisitos

- Python 3.10 o posterior.
- Una clave propia de Groq para las respuestas generativas.
- Para modo de voz: micrófono, salida de audio y dependencias de voz compatibles con Windows.

La clave que se encontraba en la versión anterior del proyecto debe considerarse comprometida: revóquela en Groq y use una clave nueva. No reutilice la clave anterior.

## Instalación en Windows PowerShell

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

Edite `.env` y establezca `GROQ_API_KEY` con una clave recién creada. No comparta ni registre ese archivo.

## Inicio

```powershell
.\.venv\Scripts\python.exe main.py
```

El modo de texto funciona sin Groq para recordar, consultar y olvidar datos guardados; otras preguntas muestran un error de configuración explícito. Para usar micrófono y voz:

```powershell
.\.venv\Scripts\python.exe main.py --voice
```

El punto de entrada original sigue disponible con `python jarvis.py`. `python modelos.py` lista los modelos de Groq si hay una clave configurada.

## Arquitectura

- `main.py`: aplicación de consola y punto de entrada.
- `core/brain.py`: conversación, memoria y coordinación, independiente de voz.
- `core/ai_provider.py`: abstracción de proveedor y cliente Groq.
- `core/context_manager.py`: límite de historial e inclusión de recuerdos relevantes.
- `core/personality.py`: instrucciones de personalidad.
- `core/config.py`: configuración desde variables de entorno y `.env`.
- `database/database.py`: almacenamiento SQLite y consultas parametrizadas.
- `voice/`: integración opcional de reconocimiento y síntesis.
- `tools/computer.py`: comandos locales conservados con reconocimiento más estricto.
- `tests/`: pruebas sin llamadas de red ni claves API reales.

SQLite se crea automáticamente en `storage/memory/jarvis_memory.db`. Se guardan sesiones y mensajes, recuerdos explícitos y algunos intercambios seleccionados sobre proyectos/decisiones. Los secretos detectados se rechazan. No se envía la base de datos completa al proveedor. `JARVIS_MAX_CONTEXT_TOKENS` y `JARVIS_MAX_MEMORY_TOKENS` limitan el contexto mediante una estimación aproximada de tokens.

## Comandos de memoria

- `Jarvis, recuerda que ...`
- `¿Qué recuerdas de mí?`
- `Jarvis, olvida ...`
- `¿Qué estábamos haciendo?`
- `Recuérdame revisar el informe el 2026-12-01 09:00`
- `Mis recordatorios`
- `Completa recordatorio 2`

Las frases de memoria y recordatorios se procesan localmente y no requieren una clave Groq. Las fechas deben usar `AAAA-MM-DD HH:MM` y se interpretan en la zona horaria local si no se incluye desplazamiento. El resto de preguntas necesita conectividad y un modelo configurado. No se ejecutan comandos arbitrarios generados por el modelo.

## HUD y herramientas con permisos

```powershell
.\.venv\Scripts\python.exe main.py --gui
```

El HUD permite conversar con respuestas en streaming, cambiar sesiones, reconocimiento de voz con botón, interrumpir reproducción, consultar CPU/RAM/disco, leer/buscar archivos permitidos dentro de `JARVIS_WORKSPACE`, abrir enlaces HTTPS y lanzar Notepad/Calculator con confirmación. El proveedor puede elegir funciones de una lista allowlist en solicitudes que parecen necesitar herramientas; no hay shell, escritura/borrado de archivos ni órdenes arbitrarias del modelo. Las respuestas a solicitudes con herramientas se resuelven antes de mostrarse, mientras que los turnos normales usan streaming.

La activación offline por voz usa Vosk en CPU y requiere un modelo acústico compatible instalado aparte; el catálogo oficial consultado no lista actualmente un modelo español pequeño. Si dispone de un modelo Vosk compatible, descomprímalo fuera del repositorio y defina `JARVIS_VOSK_MODEL_PATH` con su carpeta. En el HUD, pulse «Activar palabra Jarvis» para iniciar el detector; el audio no se envía al proveedor mientras se espera la palabra. El reconocimiento por botón sigue usando el servicio de Google.

### Base de datos de proyectos (opcional)

La integración admite SQLite, MySQL y PostgreSQL en modo de solo lectura. Defina `JARVIS_PROJECT_DB_PATH` y una lista explícita de tablas permitidas en `JARVIS_PROJECT_DB_TABLES` dentro de `.env`; instale el driver correspondiente con `pip install -r requirements.txt`. Cada consulta a tablas requiere aprobación en el HUD; el modelo no puede enviar SQL libre, modificar datos ni consultar tablas fuera de la lista. Use además una cuenta de base de datos que tenga privilegios `SELECT` solamente.

## Resúmenes de conversación

Cuando una sesión supera el tamaño de historial configurado, JARVIS resume los mensajes antiguos de forma asíncrona con el proveedor de IA, guarda el resumen y lo incluye como referencia en futuras consultas. Los mensajes fuente no se eliminan. La generación requiere una conexión Groq; si falla, JARVIS conserva el historial y registra el motivo.

## Pruebas

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Limitaciones y siguientes incrementos

Las fases están implementadas de forma incremental, no como una finalización total del asistente. El despertar local requiere un modelo Vosk compatible; el reconocimiento con botón usa Google. La animación de audio se alimenta del nivel del micrófono cuando el detector Vosk está activo. No hay automatizaciones arbitrarias, escritura/borrado de archivos ni operación wake-word cuando JARVIS está cerrado. Los comandos de hora, monitores y zoom conservan las capacidades previas; el acceso a periféricos depende del dispositivo y sus controladores.

Siguientes pasos recomendados: verificar una nueva clave Groq, validar los dispositivos de audio y un modelo Vosk compatible; probar en servidores reales los conectores SQL autorizados; y evolucionar el manejo de audio hacia full-duplex sin bloquear el HUD.
