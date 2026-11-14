# Estado de desarrollo — J.A.R.V.I.S.

## Estado general

Fase 1 y una implementación local de las principales funciones de las fases 2–5 están listas. La suite y el HUD (modo offscreen) se validaron localmente. La verificación real con Groq, micrófono, wake word con modelo español y dispositivos físicos sigue pendiente. No se declara el producto completo: las limitaciones funcionales se enumeran abajo.

## Avance por módulo

- Cerebro conversacional desacoplado de voz: implementado en `core/brain.py`.
- Personalidad y selección de contexto: implementadas en `core/personality.py` y `core/context_manager.py`.
- Memoria SQLite/sesiones/mensajes y operaciones CRUD: implementadas en `database/database.py` y `core/memory.py`.
- Proveedor Groq con configuración externa, timeout, reintentos limitados y streaming entregado a la interfaz: implementado; llamada real pendiente de una clave nueva.
- Consola, compatibilidad del script anterior y comandos locales: implementados en `main.py`, `jarvis.py` y `tools/computer.py`.
- Fase 2: HUD incluye botón push-to-talk, turnos, finalización por silencio, detención de síntesis y wake word/transcripción offline Vosk sobre CPU; se requiere instalar un modelo fuera del repositorio.
- Fase 3: Groq function calling conectado a herramientas allowlist para estado del sistema, exploración/búsqueda/lectura acotada del workspace, navegación HTTPS y lanzamiento de dos aplicaciones Windows; acciones externas requieren aprobación y no hay ejecución de shell.
- Fase 4: HUD PyQt funcional para conversación en streaming, sesiones, estado, entrada/salida de voz, herramientas y recordatorios.
- Fase 5: recuerdos de proyecto, recordatorios SQLite persistentes con notificación en el HUD, resúmenes asíncronos sin borrar mensajes originales y conector SQLite/MySQL/PostgreSQL de solo lectura con tablas allowlist y aprobación por consulta.

## Archivos creados o modificados

**Creados:** `main.py`, `.env.example`, `.gitignore`, `README.md`, `DEVELOPMENT_STATUS.md`; `core/__init__.py`, `core/ai_provider.py`, `core/brain.py`, `core/config.py`, `core/context_manager.py`, `core/conversation.py`, `core/memory.py`, `core/personality.py`, `core/reminders.py`; `database/__init__.py`, `database/database.py`, `database/migrations.py`, `database/models.py`, `database/project_database.py`; `tools/__init__.py`, `tools/computer.py`, `tools/registry.py`; `voice/__init__.py`, `voice/listener.py`, `voice/speech.py`, `voice/wake_word.py`; `interface/__init__.py`, `interface/hud.py`; `tests/__init__.py`, `tests/test_core.py`, `tests/test_compatibility.py`, `tests/test_automation.py`.

**Modificados:** `jarvis.py`, `modelos.py`, `requirements.txt`, `README.md`, `DEVELOPMENT_STATUS.md`. Se retiraron las credenciales embebidas de los scripts y se migraron al proveedor configurable.

**Ajustados en la continuación más reciente:** `core/brain.py`, `database/database.py`, `database/project_database.py`, `core/config.py`, `requirements.txt`, `tests/test_core.py`, `tests/test_automation.py`, `README.md` y este estado. Se añadieron resúmenes automáticos asíncronos, migración del estado del resumen y conectores SQLAlchemy de lectura controlada.

## Dependencias

`requirements.txt` incorpora `groq`, `psutil`, `vosk`, `SQLAlchemy`, `PyMySQL` y `psycopg[binary]`; las dependencias opcionales se instalaron en el entorno. PyAudio ya estaba instalado. Instalar en Windows con `python -m pip install -r requirements.txt`.

## Pruebas ejecutadas en esta continuación

- `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`: **32 pruebas superadas**, incluidas resumen automático, redacción de secretos, migración SQLite y conectores SQL.
- `python -m compileall -q core database interface tools voice tests`: **superado**.
- `.\.venv\Scripts\python.exe -m pip check`: **sin dependencias rotas**.
- `.\.venv\Scripts\python.exe main.py --help`: **superado**.
- Importación de 18 módulos del proyecto y dependencias opcionales (`pyaudio`, `vosk`, `psutil`, PyQt5, SQLAlchemy y drivers): **superada**.
- Prueba HUD offscreen y persistencia SQLite: **superadas previamente**.
- Pylance `workspace/diagnostic`: **sin diagnósticos**.
- Búsqueda de prefijos literales de credenciales en el código fuente: **sin coincidencias**. Se retiraron dos cachés de bytecode antiguos durante la limpieza final.
- Groq: no ejecutado; el entorno confirma que no hay `GROQ_API_KEY` configurada. No se reutilizó la clave comprometida.
- Se instaló Vosk y PyAudio está importable, pero el modelo de reconocimiento acústico español debe descargarse aparte y definirse en `JARVIS_VOSK_MODEL_PATH`; no se probó con micrófono.

## Problemas conocidos / limitaciones

- Revocar la clave Groq anteriormente incrustada en los archivos originales y configurar una nueva en `.env`; no se reutilizó.
- Los resúmenes requieren Groq configurado; no se pudo validar la llamada real.
- El reconocimiento de voz de botón depende de Google; el modo wake word/transcripción offline requiere un modelo Vosk compatible que no se incluye en el repositorio. El catálogo oficial consultado no lista actualmente un modelo español pequeño.
- La selección de recuerdos se basa en palabras clave; no equivale a búsqueda semántica.
- Los drivers de MySQL/PostgreSQL están instalados, pero no se configuró ningún servidor real para validar autenticación, permisos SELECT y compatibilidad del esquema.
- La lectura de recordatorios se activa con la aplicación abierta; no se instala como servicio ni notifica cuando JARVIS está cerrado.
- La voz usa un turno por push-to-talk o wake word; no admite full-duplex. El HUD dibuja un anillo reactivo al nivel de micrófono en modo Vosk, no una forma de onda.
- Las herramientas no permiten escribir/borrar archivos, ejecutar shell ni navegar automáticamente por la web. El acceso a bases externas admite SQLite, MySQL y PostgreSQL mediante SELECT/allowlist y aprobación, pero solo SQLite se ha probado con una base local.

Durante la validación, la prueba de migración detectó que un archivo SQLite de prueba no se cerraba explícitamente en Windows; se corrigió y la suite final de 32 pruebas pasó.

## Próximas tareas

1. Configurar una clave nueva y verificar manualmente una petición Groq, incluida una llamada de herramienta.
2. Obtener un modelo Vosk compatible y validar wake word con el micrófono objetivo; no hay modelo español pequeño en el catálogo oficial consultado.
3. Probar voz, interrupción y notificaciones en el dispositivo real.
4. Probar MySQL/PostgreSQL únicamente con endpoints y cuentas de solo lectura autorizados.
5. Considerar voz full-duplex y activación wake-word como servicio independiente, con permisos y consumo revisados.

## Reanudación

Desde PowerShell, activar `.venv`, instalar `requirements.txt`, ejecutar la suite `unittest` y arrancar `python main.py --gui` o `python main.py`. Configure una clave Groq nueva localmente en `.env` (no compartirla). Para wake word, configure `JARVIS_VOSK_MODEL_PATH` con un modelo Vosk compatible que haya obtenido por separado; no se ha verificado disponibilidad de un modelo español pequeño.
