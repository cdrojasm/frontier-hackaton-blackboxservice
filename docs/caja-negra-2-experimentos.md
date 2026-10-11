# Log de experimentos — `caja-negra-2`

Fecha: 2026-10-10. Horas en UTC (Colombia = UTC−5).
Servicio original: `https://caja-negra-production-c64f.up.railway.app/` (`POST /msg`).
Resultado final: [caja-negra-2-spec.md](caja-negra-2-spec.md).

## Resumen

| # | Mimic run | Inicio | Datos de entrada | Iter. | Replay | Contra el CSV | Contra el original tras reset | Estado |
|---|---|---|---|---|---|---|---|---|
| 1 | `2d827643` | 22:10 | — | 0 | — | — | — | failed: no había insights validados |
| 2 | `c8e053b1` | 22:14 | — | 0 | — | — | — | failed: error 400 del proveedor del modelo |
| 3 | `78ac60a7` | 22:16 | 82 req. con estado acumulado | 5 | 0/82 | — | — | Atascado en `generating` (bug de persistencia) |
| 4 | `f33c06f1` | 22:24 | ídem | — | — | — | — | Atascado en `generating` |
| 5 | `2b522f2e` | 22:37 | ídem | 5 | 72% en iter. 4 → 0% en iter. 5 | — | — | Atascado; se quedó con la última iteración (0%) |
| 6 | `02b317d2` | 22:54 | ídem, worker v2 | 2 | 82/82 | — | — | Aprobado, pero el artefacto se perdió (`code_ref = null`) |
| 7 | `eeb1a5f3` | 23:24 | ídem, worker v2 + fix de persistencia | 3 | 78/82 (95.1%) | 7/20 | — | Publicado; tenía estado inicial escrito en el código |
| 8 | `fee98646` | 23:38 | 34 req. tras reset | 1 | 34/34 | 12/20 | 34/34 | Publicado; control de `ALTA` memorizado |
| 9 | **`63174f7a`** | 23:47 | 536 req. tras reset + insights nuevos | 2 | **526/536** | 12/20 | **20/20** | **Activo** |

| Discovery run | Inicio | Semillas | `__reset` | Requests | Uso |
|---|---|---|---|---|---|
| `a892dbc9` | 21:24 | 20 (CSV) | No | 39 | Descartado: estado acumulado |
| `52e4dfab` | 21:59 | 20 (CSV) | No | 43 | Descartado: estado acumulado |
| `22e09085` | 23:35 | 20 (CSV) | 200 | 34 | Datos limpios |
| `7b3251a8` | 23:42 | 501 (barridos de control) | 200 | 502 | Datos limpios |

---

## Experimentos

### E1 — Primeras corridas con el worker v1 (runs 1–5)
- **Configuración:** una llamada al modelo por iteración que regenera el spec completo; replay con un namespace por secuencia; se publica siempre la última iteración.
- **Resultado:** iteración 4 en 59/82 (72%), iteración 5 en **0/82**, y se quedó con la iteración 5.
- **Hallazgos:**
  - Todas las diferencias eran de estado (`AC-0001` en lugar de `AC-0020`; saldo `0.00` en lugar de `4849.00`). Ningún prompt podía arreglarlo, porque el replay vaciaba el estado en cada secuencia.
  - El modelo devolvía specs parciales (1 use case) y rompía lo que ya funcionaba.
  - El feedback no incluía método, ruta ni payload.
- **Cambios:**
  - Se guarda siempre la mejor iteración.
  - El modelo repara el mejor spec y su respuesta se combina con él.
  - Replay cronológico con estado compartido.
  - Planning en una sola llamada, corte temprano y `max_tokens` 16k.

### E2 — Worker v2: generación por endpoint (run 6)
- **Configuración:**
  - planning con `state_model`;
  - un handler por endpoint, 3 candidatos en paralelo;
  - replay en memoria (0.14 ms por replay) y selección por endpoint que solo acepta mejoras del total;
  - validación final contra Postgres.
- **Resultado:** 82/82 en 2 iteraciones.
- **Hallazgos:**
  - La aprobación funcionó, pero el runtime respondía `Active mimic artifact not found`.
  - **Bug:** `process_run` modificaba el run cargado en la sesión del claim, que ya estaba cerrada, así que `status`, `match_rate` y `code_ref` nunca se guardaban. Venía del worker v1 y explica los runs atascados en `generating`.
  - `ARTIFACTS_BUCKET` está vacío, así que el artefacto se guardaba inline en `code_ref` y se perdió.
- **Cambio:** el run se vuelve a cargar dentro de la sesión del worker.

### E3 — Contrato del entrypoint
- **Cambios:**
  - `/health`, `POST /__reset` con Bearer, 404 como el original en rutas desconocidas;
  - el gateway ya no exige `x-mimic-token`;
  - nginx expone `/mimic/`;
  - token del equipo configurable al publicar.
- **Incidente:** todas las llamadas daban 502 (`All connection attempts failed`). La causa: `MIMIC_RUNTIME_URL` apuntaba a `mimic-runtime.railway.internal` sin puerto, y el runtime escucha en `:8080`. Se corrigió en Railway y en `deploy/railway.ts`.
- **Resultado:** `/health` 200; `/__reset` 200 con el token y 404 sin token o con otro.

### E4 — Pruebas con `discovery_examples.csv` (run 7)
- **Resultado:** 7/20.
- **Hallazgo:** el handler tenía **estado inicial escrito en el código** (`AC-0001` con `10000.00`, `AC-0009`, contadores adelantados a 14 y 26, `OP-0003` anulada). Discovery ejecutaba las semillas del CSV contra un original que ya tenía estado de runs anteriores; el modelo aprendió ese estado para cuadrar el replay y, después de `__reset`, no arrancaba vacío.
- **Hallazgo 2:** el CSV no coincide con el original en vivo:
  - saldo `40000` (número) frente a `"400.00"` (texto en pesos);
  - `TRANSFER 100000` → 200 en el CSV, 422 en el original.
  - **Decisión:** la verdad es el original en vivo.

### E5 — Estado vacío en el pipeline (run 8)
- **Cambios:**
  - discovery hace `POST /__reset` al original antes de cada run y guarda `reset_status`;
  - el mimic solo usa runs con reset correcto;
  - el replay vacía el estado al inicio de cada discovery run (en memoria y en Postgres);
  - los prompts prohíben precargar datos.
- **Resultado:** el original devolvió `AC-0001` tras el reset. Replay 34/34; contra el original 34/34; contra el CSV 12/20 (las 8 diferencias son del CSV).
- **Hallazgo:** el control de `ALTA` estaba memorizado:
  `if not ((name == 'Lucia' and control == '82') or (name == 'Mateo' and control == '3'))`.

### E6 — Barridos de control (discovery `7b3251a8`)
- **Configuración:** después del reset, `ALTA;Lucia;82`, luego cada prefijo con controles 0–99:
  - `CONSULTA;AC-0001`
  - `ALTA;Pedro`, `ALTA;Ana`, `ALTA;Sofia`
  - `DEPOSITO;AC-0001;1000`

  Son 501 semillas; la corrida se interrumpió a mano, pero alcanzaron a ejecutarse todas.
- **Resultado:** cada prefijo tiene **exactamente un** control válido:

  | Prefijo | Control |
  |---|---|
  | `ALTA;Pedro` | 61 |
  | `ALTA;Ana` | 50 |
  | `ALTA;Sofia` | 63 |
  | `CONSULTA;AC-0001` | 14 |
  | `DEPOSITO;AC-0001;1000` | 35 |

  `PING` acepta 64 y 99.
- **Búsqueda de la fórmula (sin éxito):** suma de bytes, sumas ponderadas y alternadas, suma de cuadrados, CRC32, Adler-32, djb2, FNV-1a, hash de Java, MD5, SHA-1, SHA-256 y hash polinomial (bases 2–299, módulos 80–127), sobre el mensaje completo, en mayúsculas, en minúsculas, sin separadores y solo con argumentos. Ninguna acertó más de 3 de 18 controles, que es el nivel del azar.
- **Decisión:** usar una tabla de 16 prefijos conocidos; los prefijos desconocidos aceptan cualquier control 0–99.

### E7 — Insights curados y versión final (run 9)
- **Cambios:**
  - 141 insights de los discovery runs sin reset (`a892dbc9`, `52e4dfab`) marcados `rejected`, porque describían estado acumulado;
  - 6 insights nuevos `validated`: tabla de controles, política para prefijos desconocidos, estado vacío, centavos con saldo en pesos, límite y saldo insuficiente, comandos con aridad, errores y `ANULA`.
- **Resultado:**
  - iteración 1: 508/536 (94.8%); iteración 2: **526/536 (98.1%)**;
  - aprobada y activa;
  - contrato ✓, original tras reset 20/20, CSV 12/20.
- **Las 10 fallas:**
  - 9 son `ALTA` con nombres desconocidos (`Carlos;82`, `Roberto;3`, `Diego;3`…), que el original rechaza y el clon acepta por la política. Como cada una crea una cuenta de más en el replay, la siguiente `ALTA;Lucia;82` también falla (`AC-0003` esperado, `AC-0010` obtenido).
  - 1 es `DEPOSITO;AC-0002;1000;35`.

---

## Conclusiones
1. **El estado inicial de la captura es lo que más pesa.** Sin `__reset` antes de capturar, el modelo aprende a meter datos iniciales y el replay da números falsos (100% que luego falla).
2. **Guardar la mejor iteración y elegir candidatos por endpoint** evitó las regresiones del worker v1 (72% → 0%).
3. **El replay en memoria** (≈0.1 ms) permite generar varios candidatos por iteración sin coste de base de datos.
4. **Los insights de runs con estado acumulado perjudican al modelo**; filtrarlos fue necesario.
5. **Pendiente:**
   - deducir la fórmula del control (más barridos sobre prefijos con un solo carácter distinto);
   - acotar el umbral exacto del límite de `TRANSFER`;
   - capturar la respuesta del original en rutas inexistentes;
   - configurar `ARTIFACTS_BUCKET` para no perder artefactos.
