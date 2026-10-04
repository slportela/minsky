# Comparación exploratoria del backup y la entrega actual

Fecha: 2026-10-03. Fuente: bucket oficial, prefijos `data_backup_20260831/` y `data/`. No se modificó la fuente ni se mezclaron versiones en el pipeline.

## Alcance y método

Se descargaron y verificaron por tamaño y ETag 39 archivos del backup (127.103.050 bytes): customers (150.000), products (400.000), service_agents (1.200), más 12 particiones de cada una de transactions, call_center_interactions y complaints. Se compararon contra los CSV originales actuales, sin transformaciones de silver. Las 12 fechas, espaciadas sobre las particiones disponibles del backup, son: 2023-07-01, 2023-08-11, 2023-09-21, 2023-11-01, 2023-12-12, 2024-01-22, 2024-03-04, 2024-04-14, 2024-05-25, 2024-07-05, 2024-08-15, 2024-09-25. Muestra exploratoria no aleatoria; no representa todo el backup. Comparaciones de registros por PK; asociaciones de productos con sus clientes dentro de cada versión.

## Diferencias entre versiones

Los esquemas de las seis tablas coinciden. No hay duplicados de PK dentro del alcance leído. Esto no prueba ausencia de duplicados en otras fechas ni en otras claves.

| Tabla | Filas backup | Filas actuales, mismo alcance | IDs compartidos |
|---|---:|---:|---:|
| customers | 150.000 | 150.000 | 4.025 |
| products | 400.000 | 400.000 | 128.599 |
| service_agents | 1.200 | 1.200 | 604 |
| transactions | 54.565 | 51.254 | 51 |
| call_center_interactions | 8.011 | 7.505 | 73 |
| complaints | 750 | 750 | 750 |

Todos los 128.599 productos con ID compartido tienen distinto customer_id. En los 750 reclamos compartidos solo cambian customer_id (750), affected_product_id (492) y assigned_agent_id (255). Los otros atributos, incluidos fechas, estado, importes y textos, permanecen iguales. Sugiere regeneración o reasignación de referencias; sin el generador no se puede establecer el proceso exacto. La entrega actual no parece ser una simple extensión incremental del backup. No se pueden usar IDs entre versiones como si identificaran la misma entidad histórica.

## Calidad y señal

| Comprobación | Backup | Actual |
|---|---:|---:|
| Reclamos cuyo producto informado pertenece a otro cliente | 492/492 | 492/492 |
| Reclamos sin origin_interaction_id | 750/750 | 750/750 |
| Transacciones con propietario de producto correcto | 54.565/54.565 | 51.254/51.254 |
| Transacciones anteriores al registro del cliente | 15.888/54.565 | 15.059/51.254 |
| Transacciones anteriores a la apertura del producto (comparación por fecha) | 15.833/54.565 | 14.971/51.254 |
| Score frente a días de mora, Pearson r (pares no nulos, todos los productos) | −0,00509 (n=106.407) | −0,00376 (n=106.905) |
| Transacciones marcadas fraude | 53/54.565 | 48/51.254 |
| fraud_score>30 implica fraude | 25/25 | 31/31 |

No se encontró una reparación de la asociación entre reclamo, cliente y producto al usar exclusivamente el backup. Las incoherencias temporales también aparecen en ambos. La muestra de fraude es pequeña; no permite descartar predictores raros. El umbral observado es sospecha de fuga, no mejora predictiva legítima.

La asociación entre motivo de contacto y resolución en primer contacto aparece en ambas versiones: Queja 541/1.347 resueltos frente a 530/1.226 en actual; Transaccional 2.563/2.797 frente a 2.425/2.630. No se probó que exista una señal nueva ni que un modelo mejore un baseline.

## Corrección de H7: tasas de interés

El análisis anterior confundió promedios por producto con tasas constantes. Es un error nuestro y no evidencia de una mejora del backup. En ambas versiones, tarjetas tienen 2.701 tasas distintas no nulas (18–45 en unidades de la fuente), préstamos personales 1.601 (12–28), hipotecas 601 (6–12), ahorro 251 (1–3,5), corriente 41 (0,1–0,5) e inversión 501 (3–8). Solo seguros y débito tienen una tasa no nula constante, cero.

Por tipo con tasa variable, las correlaciones lineales entre tasa y score o ingreso son débiles en ambas versiones (|r|≤0,024). Para tarjetas, tasa–score r=0,00081 con 76.197 pares válidos en backup y r=0,00492 con 76.831 en actual. La existencia de variación no garantiza señal; estas correlaciones no descartan relaciones no lineales u otras variables. Se corrigieron docs/data_findings.md, la interpretación del notebook H7 y Campos!H36:I36/N36 del Google Sheet.

## Conclusión y límites

Dentro del alcance analizado no hay evidencia de que el backup tenga más señal útil que la entrega actual. Sí hay evidencia de que las entidades y sus referencias cambian sustancialmente entre entregas. No se recomienda mezclar versiones ni cambiar la fuente oficial por esta muestra. No se hizo entrenamiento, selección, evaluación held-out ni inferencia sobre todos los campos. No se descargaron campaign_sends ni digital_events del backup; tampoco hay transcripts ni satisfaction_surveys allí según el inventario. Cualquier conclusión de ausencia de señal debe limitarse a las relaciones comprobadas.

Los resultados exactos, esquemas, fechas, campos cambiados y denominadores están en backup-comparison-20261003.json. Los conteos cubren exhaustivamente los archivos seleccionados; no se estiman intervalos poblacionales sobre esta selección no aleatoria.
