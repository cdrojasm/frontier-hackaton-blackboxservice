# Documento de diseño

## Introducción

Este documento describe una solución para descubrir y clonar servicios de software
que deben ser tratados como cajas negras. El único conocimiento inicial disponible
es la URL del servicio y su contrato, complementado por ejemplos de solicitudes y
respuestas reales.

El objetivo no es únicamente comprender el comportamiento observable del servicio,
sino construir un reemplazo funcionalmente equivalente desde la perspectiva del
consumidor. El servicio clonado debe conservar el contrato de entrada y producir
respuestas compatibles, aunque su implementación interna, tecnología y modelo de
datos sean diferentes.

La solución debe ser capaz de inferir la estrategia general del servicio mediante
una fase de *discovery* y posteriormente generar o configurar los componentes
necesarios para su ejecución: API, lógica de negocio, persistencia o emulación de
persistencia y reglas de consistencia. Por ejemplo, ante un servicio bancario que
permite crear cuentas, depositar, retirar fondos y cancelar cuentas, el clon debe
reproducir esos comportamientos y mantener un estado equivalente, incluso si se
implementa sobre una base de datos o tecnología distinta.

La motivación principal es modernizar o reemplazar servicios legacy cuya tecnología
no está documentada, cuyo código fuente no está disponible o que presentan un alto
riesgo operativo. La arquitectura propuesta sigue el flujo:

`Service discovery → Service clone → Ship`

## AS-IS

Existe una gran cantidad de servicios legacy que funcionan como dependencias críticas
pero presentan una o varias de las siguientes condiciones:

- Tecnología, arquitectura y lógica interna desconocidas.
- Documentación incompleta o desactualizada.
- Imposibilidad de acceder al código fuente o al equipo que lo mantiene.
- Comportamiento dependiente de estado, datos históricos o reglas implícitas.
- Riesgo de fallos inesperados, degradación de performance o indisponibilidad.

El conocimiento disponible se limita a la URL del servicio, su contrato y un conjunto
de ejemplos de interacción. Actualmente no existe un proceso automatizado para
descubrir el comportamiento completo, validar la equivalencia ni producir un
reemplazo desplegable.

## TO-BE

La solución estará compuesta por tres etapas principales:

1. **Service discovery:** recibe la URL, el contrato y ejemplos de tráfico; ejecuta
   pruebas controladas contra el servicio original; identifica endpoints, estados,
   validaciones, dependencias, reglas de negocio, errores, efectos secundarios y
   características de performance.
2. **Service clone:** transforma los insights descubiertos en una especificación
   ejecutable y genera o configura el clon, incluyendo API, lógica de negocio,
   persistencia o emulación de estado, datos iniciales y manejo de errores.
3. **Ship:** valida el clon frente al servicio original, presenta los resultados de
   equivalencia y permite empaquetar y desplegar el servicio en el entorno objetivo.

El usuario podrá observar el progreso, los hallazgos, las incertidumbres y los
resultados de validación desde una interfaz web. Después de la clonación podrá
comparar solicitudes y respuestas del servicio original y del clonado, incluyendo
compatibilidad funcional y métricas de performance.

## Casos de uso

### UC-01: Registrar un servicio caja negra

El usuario proporciona la URL del servicio, su contrato y ejemplos de interacción.
Los ejemplos pueden cargarse mediante una tabla en la interfaz o mediante un archivo
CSV con las columnas:

`verbo | path | payload | response_code | response_content`

### UC-02: Ejecutar discovery

El sistema ejecuta pruebas de discovery de forma controlada, observa las respuestas y
construye un modelo de comportamiento. El proceso debe registrar evidencia, nivel de
confianza, supuestos y preguntas o comportamientos que no pudieron determinarse.

### UC-03: Generar el clon

Con base en el modelo descubierto, el sistema genera el contrato ejecutable, la API,
la lógica interna, el almacenamiento o emulador de estado y la configuración necesaria
para ejecutar el servicio clonado.

### UC-04: Consultar el estado de la clonación

El usuario consulta desde la UI el avance de las fases de discovery y clonación,
incluyendo tareas completadas, tareas en progreso, errores, hallazgos y bloqueos.

### UC-05: Comparar el servicio original y el clonado

El usuario ejecuta una solicitud contra ambos servicios y compara código de respuesta,
headers relevantes, estructura y contenido de la respuesta, efectos sobre el estado y
métricas como latencia, throughput y tasa de error.

### UC-06: Validar y publicar

El sistema ejecuta una suite de regresión con los ejemplos suministrados y los casos
descubiertos. Si los criterios de equivalencia se cumplen, el usuario puede empaquetar
y enviar el clon al entorno de destino.

## Feature customer

Las capacidades principales orientadas al cliente son:

- **Alta de proyectos y servicios:** crear proyectos, registrar servicios y gestionar
  versiones de contratos, ejemplos y clones.
- **Carga flexible de evidencia:** ingresar ejemplos manualmente mediante tabla o
  cargarlos por CSV con validación de formato y campos obligatorios.
- **Configuración de discovery:** definir límites de requests, timeouts, headers,
  credenciales, ambientes y políticas de seguridad para las pruebas.
- **Visibilidad del proceso:** consultar progreso, logs, eventos, hallazgos,
  incertidumbres y nivel de confianza del modelo generado.
- **Explorador del comportamiento:** visualizar endpoints, esquemas, estados,
  transiciones, reglas inferidas, errores y dependencias observadas.
- **Generación del clon:** crear una versión clonada reproducible y versionada, con
  configuración de persistencia y datos iniciales.
- **Comparador original vs. clon:** ejecutar casos individuales o suites completas y
  visualizar diferencias funcionales y de performance.
- **Criterios de aceptación:** definir tolerancias para códigos, payloads, headers,
  latencia y consistencia antes de aprobar una clonación.
- **Ship y operación:** exportar artefactos, desplegar el clon y consultar su estado
  operativo.
- **Auditoría y colaboración:** conservar historial de cambios, decisiones,
  aprobaciones, evidencias y responsables.

## Performance

### Principales bottlenecks del servicio a clonar

- Latencia variable o alta del servicio original.
- Rate limits, timeouts, circuit breakers y restricciones de concurrencia.
- Dependencias externas lentas o inestables.
- Operaciones de lectura y escritura sobre almacenamiento persistente.
- Flujos que requieren múltiples requests secuenciales o tokens de sesión.
- Generación de respuestas grandes o transformaciones costosas.
- Comportamiento sensible al estado, al orden de las operaciones o a la carga.
- Límites del ambiente donde se ejecuta el clon: CPU, memoria, red y almacenamiento.

### Performance del discovery y clonación

El discovery debe usar presupuestos configurables de tiempo, requests y concurrencia
para evitar afectar al servicio original. Las pruebas deben ejecutarse con límites,
reintentos controlados y pausas cuando se detecten rate limits o degradación.

Mientras el LLM realiza el discovery y plantea la implementación, el sistema debe
modelar progresivamente los insights, por ejemplo:

- Endpoint, método y esquema observado.
- Precondiciones, postcondiciones y transiciones de estado.
- Reglas de validación y normalización.
- Dependencias entre operaciones.
- Patrones de error y códigos de respuesta.
- Campos deterministas, variables y sensibles al tiempo.
- Hipótesis, evidencia que las soporta y nivel de confianza.
- Casos aún no explorados o que requieren confirmación.

Estos insights deben persistirse como artefactos versionados y no únicamente como
texto generado. Así se puede reanudar el proceso, auditar decisiones, detectar
contradicciones y volver a generar el clon sin repetir todo el discovery.

El clon debe medirse en dos dimensiones: equivalencia funcional y eficiencia. Como
mínimo se deben observar latencia p50/p95/p99, throughput, tasa de errores, uso de
CPU y memoria, saturación de almacenamiento y comportamiento bajo concurrencia.

## Volumetría

Como capacidad inicial empresarial, se propone soportar aproximadamente **1.000
servicios** registrados y gestionados dentro de la plataforma. Esta cifra debe
entenderse como capacidad de inventario y operación, no necesariamente como 1.000
discoveries ejecutándose de forma simultánea.

La volumetría inicial sugerida es:

- Hasta 1.000 servicios registrados por instalación empresarial.
- Hasta 100 proyectos o equipos activos, sujeto al modelo de tenancy.
- Hasta 10 discoveries concurrentes por instalación, parametrizable según costo y
  límites del servicio objetivo.
- Entre 100 y 1.000 ejemplos de requests por servicio en el flujo inicial.
- Persistencia de versiones de contratos, evidencias, insights y artefactos generados.
- Capacidad de ejecutar suites de regresión sobre múltiples versiones de un clon.

La arquitectura debe permitir escalar horizontalmente los workers de discovery,
generación, validación y comparación sin acoplarlos a la UI. La capacidad definitiva
debe validarse con pruebas de carga y con el tamaño real de los contratos y payloads.

## Seguridad

- **Protección de credenciales:** almacenar tokens, API keys, certificados y headers
  sensibles en un gestor de secretos; nunca persistirlos en logs, CSV sin protección
  ni prompts del LLM.
- **Control de acceso:** aplicar autenticación, autorización por proyecto y servicio,
  roles, mínimo privilegio y separación entre operadores, revisores y administradores.
- **Aislamiento por tenant:** separar datos, artefactos, logs, credenciales y workers
  entre clientes o proyectos.
- **Ejecución segura:** ejecutar el código generado en sandboxes aislados, con límites
  de CPU, memoria, red, filesystem y tiempo; impedir acceso no autorizado al host.
- **SSRF y egress:** validar URLs de destino, bloquear rangos privados y metadata
  endpoints, aplicar allowlists y controlar las salidas de red durante discovery.
- **Protección del servicio original:** aplicar rate limits, timeouts, circuit breakers,
  límites de concurrencia y ventanas de prueba para evitar una denegación de servicio.
- **Seguridad del LLM:** separar instrucciones del sistema de contenido observado,
  prevenir prompt injection desde respuestas del servicio y validar toda salida antes
  de convertirla en código o configuración ejecutable.
- **Cadena de suministro:** escanear dependencias e imágenes, firmar artefactos,
  mantener SBOM y controlar las versiones del código y modelos utilizados.
- **Auditoría:** registrar quién ejecutó discovery, qué datos utilizó, qué cambios
  aprobó y qué artefacto fue desplegado, sin exponer secretos en los logs.
- **Validación antes de publicar:** ejecutar pruebas de seguridad, regresión,
  equivalencia y aislamiento antes de permitir el ship del clon.
