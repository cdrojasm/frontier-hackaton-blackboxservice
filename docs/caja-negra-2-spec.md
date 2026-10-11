# Especificación del servicio clonado — `caja-negra-2`

Especificación del reemplazo publicado, generada a partir del artefacto activo.

| | |
|---|---|
| Mimic run activo | `63174f7a-42c8-4f7f-9c0a-346032bf39cd` |
| Iteración aprobada | 2 (validada contra Postgres) |
| Replay | **526/536 (98.1%)** sobre 2 discovery runs capturados tras `POST /__reset` |
| Use cases | 1 (`POST /msg`) |
| Original clonado | `https://caja-negra-production-c64f.up.railway.app/` |

Historial de cómo se llegó aquí: [caja-negra-2-experimentos.md](caja-negra-2-experimentos.md).

---

## 1. Entrypoint

```
URL base:  https://app-production-357cf.up.railway.app/mimic/caja-negra-2
Header:    Authorization: Bearer <token del equipo>
```

| Método y ruta | Respuesta |
|---|---|
| `GET /health` | `200 {"status":"ok"}` |
| `POST /__reset` con el token del equipo | Borra todo el estado (cuentas, operaciones y contadores) → `200 {"status":"reset"}` |
| `POST /__reset` sin token o con otro token | Se trata como ruta inexistente → `404 {"detail":"Not Found"}` |
| `POST /msg` | Procesador de mensajes (secciones 2–6) |
| Cualquier otra ruta o método | `404 {"detail":"Not Found"}` |

**Despliegue:** `app` (nginx, público) → `api` (gateway `/mimic/{slug}`) → `mimic-runtime`
(privado, `:8080`). El runtime lee de Postgres en cada request qué artefacto está activo, así
que aprobar otro mimic cambia el comportamiento sin redesplegar.

**Estado:** se guarda en `mimic_records` (namespace `live`). Si se envía el header
`x-mimic-namespace`, el estado queda aislado en ese namespace.

---

## 2. Protocolo

```http
POST /msg
{"m": "COMANDO;arg1;...;control"}
```

1. Si `m` falta o no es un string → `400 E301`.
2. Se separa por `;`. El comando se pasa a **mayúsculas** (`CONSULTa` ≡ `CONSULTA`).
3. Validaciones en este orden:

| Paso | Regla | Error |
|---|---|---|
| 1 | El comando existe | `400 {"error":"E301","mensaje":"comando desconocido "}` *(espacio final)* |
| 2 | Número exacto de campos (aridad) | `400 {"error":"E302","mensaje":"aridad inválida"}` |
| 3 | El control es un entero | `400 {"error":"E201","mensaje":"control mal formado"}` |
| 4 | El control es válido (sección 3) | `400 {"error":"E202","mensaje":"control inválido"}` |
| 5 | Reglas del comando (sección 5) | `E401`, `E402`, `E506`, `E507` |

### Aridad (campos totales, incluido el control)

| Comando | Campos | Formato |
|---|---|---|
| `PING` | 2 | `PING;ctl` |
| `ALTA` | 3 | `ALTA;nombre;ctl` |
| `CONSULTA` | 3 | `CONSULTA;cuenta;ctl` |
| `DEPOSITO` | 4 | `DEPOSITO;cuenta;monto;ctl` |
| `TRANSFER` | 5 | `TRANSFER;origen;destino;monto;ctl` |
| `ANULA` | 3 | `ANULA;operacion;ctl` |

---

## 3. Validación del control

El prefijo es el mensaje sin el último campo, con el comando en mayúsculas.

| Caso | Regla |
|---|---|
| Prefijo en la tabla | Solo se acepta ese control |
| `PING` | Se acepta cualquier entero |
| Prefijo desconocido | Se acepta cualquier entero entre `0` y `99` |

| Prefijo | Control | | Prefijo | Control |
|---|---|---|---|---|
| `ALTA;Ana` | 50 | | `DEPOSITO;AC-0001;1000` | 35 |
| `ALTA;Lucia` | 82 | | `DEPOSITO;AC-0001;250000` | 86 |
| `ALTA;Mateo` | 3 | | `DEPOSITO;AC-0002;700` | 35 |
| `ALTA;Pedro` | 61 | | `TRANSFER;AC-0001;AC-0002;40000` | 86 |
| `ALTA;Sofia` | 63 | | `TRANSFER;AC-0001;AC-0002;100000` | 12 |
| `CONSULTA;AC-0001` | 14 | | `TRANSFER;AC-0001;AC-0002;120000` | 55 |
| `CONSULTA;AC-0002` | 11 | | `TRANSFER;AC-0002;AC-0001;50000` | 23 |
| `CONSULTA;AC-0009` | 87 | | `ANULA;OP-0003` | 66 |

> La tabla sale de barridos 0–99 contra el original: cada prefijo tiene un solo control
> válido. La fórmula no se pudo deducir, por eso los prefijos desconocidos se aceptan con
> cualquier control (ver sección 7).

---

## 4. Modelo de estado

Arranca **vacío** tras `__reset`; el handler no precarga datos.

**`cuentas`** (clave `cuenta_id`)

| Campo | Tipo | Notas |
|---|---|---|
| `cuenta_id` | string | `AC-%04d`, contador propio desde 1 |
| `nombre` | string | Nombre recibido en `ALTA` |
| `saldo_centavos` | int | Empieza en 0 |

**`operaciones`** (clave `operacion_id`)

| Campo | Tipo | Notas |
|---|---|---|
| `operacion_id` | string | `OP-%04d`, contador compartido por `DEPOSITO` y `TRANSFER` |
| `tipo` | string | `DEPOSITO` o `TRANSFER` |
| `monto_centavos` | int | |
| `cuenta_origen` | string \| null | `null` en depósitos |
| `cuenta_destino` | string | |
| `estado` | string | `activa` o `anulada` |

Los contadores solo avanzan cuando la operación tiene éxito.

---

## 5. Comandos

### `PING`
`200 {"pong":true}`

### `ALTA;nombre;ctl`
Crea la cuenta con saldo 0 → `201 {"cuentaId":"AC-0001"}`. Un nombre repetido crea otra cuenta.

### `CONSULTA;cuenta;ctl`
- `200 {"cuenta_id":"AC-0001","saldo":"2100.00"}`. El saldo es un string en pesos con 2 decimales (`centavos / 100`).
- Cuenta inexistente → `404 {"error":"E401","mensaje":"cuenta inexistente"}`.

### `DEPOSITO;cuenta;monto;ctl`
- `monto` en centavos. Sin límite.
- Cuenta inexistente → `404 E401`.
- Éxito: suma al saldo, crea la operación → `200 {"status":"recieved","operacionId":"OP-0001"}`.

### `TRANSFER;origen;destino;monto;ctl`
Validaciones en este orden:
1. `monto >= 100000` → `422 {"error":"E506","mensaje":"límite excedido"}`
2. Origen o destino inexistente → `404 E401`
3. Saldo del origen menor que `monto` → `422 {"error":"E507","mensaje":"saldo insuficiente"}`

Éxito: descuenta del origen, suma al destino, crea la operación → `200 {"status":"recieved","operacionId":"OP-000N"}`.

### `ANULA;operacion;ctl`
| Situación | Respuesta |
|---|---|
| Operación inexistente | `404 {"error":"E402","mensaje":"operación inexistente"}` |
| Primera anulación | `200 {"ok":true,"anulada":"OP-000N"}` (marca `estado = anulada`) |
| Ya anulada | `200 {"ok":false,"motivo":"ya anulada"}` |

La anulación **no revierte saldos**.

---

## 6. Ejemplo (secuencia de referencia tras `__reset`)

| # | `m` | HTTP | Respuesta |
|---|---|---|---|
| 1 | `PING;64` | 200 | `{"pong":true}` |
| 2 | `ALTA;Lucia;83` | 400 | `E202 control inválido` |
| 3 | `ALTA;Lucia;82` | 201 | `{"cuentaId":"AC-0001"}` |
| 4 | `ALTA;Mateo;3` | 201 | `{"cuentaId":"AC-0002"}` |
| 5 | `CONSULTA;AC-0001;14` | 200 | `saldo "0.00"` |
| 6 | `DEPOSITO;AC-0001;250000;86` | 200 | `OP-0001` |
| 7 | `TRANSFER;AC-0001;AC-0002;40000;86` | 200 | `OP-0002` |
| 8 | `CONSULTA;AC-0002;11` | 200 | `saldo "400.00"` |
| 9 | `TRANSFER;AC-0001;AC-0002;120000;55` | 422 | `E506 límite excedido` |
| 10 | `TRANSFER;AC-0001;AC-0002;100000;12` | 422 | `E506 límite excedido` |
| 11 | `CONSULTa;AC-0001;14` | 200 | `saldo "2100.00"` |
| 12 | `SALDOS;AC-0001;38` | 400 | `E301 comando desconocido ` |
| 13–14 | `ANULA;OP-0003;66` | 404 | `E402 operación inexistente` |
| 15 | `CONSULTA;AC-0009;87` | 404 | `E401 cuenta inexistente` |
| 16 | `TRANSFER;AC-0002;AC-0001;50000;23` | 422 | `E507 saldo insuficiente` |
| 17 | `DEPOSITO;AC-0002;700;5X` | 400 | `E201 control mal formado` |
| 18 | `DEPOSITO;AC-0002;700;35` | 200 | `OP-0003` |
| 19 | `CONSULTA;AC-0002;11` | 200 | `saldo "407.00"` |
| 20 | `ALTA;Lucia;Gomez;11` | 400 | `E302 aridad inválida` |

Idéntica al original en vivo (20/20).

---

## 7. Diferencias conocidas con el original

| Aspecto | Clon | Original | Evidencia |
|---|---|---|---|
| Control de prefijos desconocidos | Acepta cualquier entero 0–99 | Exige un único valor (fórmula desconocida) | Las 10 fallas del replay (ej. `ALTA;Diego;3` → original 400, clon 201) |
| Umbral del límite de `TRANSFER` | `>= 100000` | Entre 50001 y 100000 | 50000 pasa y 100000 falla en el original |
| `DEPOSITO`/`TRANSFER` a cuenta inexistente | `404 E401` | No observado | Lo infirió el modelo |
| Efecto de `ANULA` sobre saldos | No revierte | No observado | — |
| Respuesta exitosa de `ANULA` | `{"ok":true,"anulada":…}` | Solo aparece en el CSV | — |
| Rutas inexistentes | `404 {"detail":"Not Found"}` | No capturado | Respuesta por defecto del runtime |
| Errores internos del handler | `500 {"error":"Generated handler failed",…}` | — | Ninguno en el replay |
