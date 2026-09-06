# Decisiones técnicas del master ledger y los adapters

Documento vivo. Cada decisión indica opciones consideradas, cuál se adopta y por qué, para poder auditarla más adelante sin repetir el análisis.

## 1. Filas `DELIVERY/MIGRATION` en el extracto de Trade Republic

### 1.1 El hecho observado

En el extracto real revisado, el 16/06/2025 aparecen pares de filas para **cada posición abierta** (acciones, cripto...) en el mismo minuto: una con la cantidad en negativo y otra con la misma cantidad en positivo, mismo ISIN, mismo precio, sin `amount` (sin efecto en efectivo). Ocurre a la vez para todos los activos que el usuario tenía en cartera ese día — no es un evento de un activo concreto, es un evento de la cuenta completa. Todo apunta a una migración técnica interna del broker (p. ej. cambio de sistema de custodia), no a una compraventa real: no cambia ni lo que posees ni tu efectivo.

### 1.2 Por qué importa

Un motor de coste FIFO que procesara estas filas literalmente cerraría el lote de compra original (con su fecha y precio reales) en la fila de salida, y abriría un lote nuevo en la fila de entrada fechado y valorado ese mismo 16/06. Si el precio de migración no coincide exactamente con el coste medio real (en la muestra, muy cerca pero no se puede garantizar que sea exacto siempre), esto reescribe en silencio la base de coste de cada posición — justo lo que la regla 9 de `CLAUDE.md` (exactitud al céntimo) prohíbe.

### 1.3 Opciones

1. **Filtrar en el adapter**: el adapter reconoce el patrón (mismo día + ISIN + precio + cantidad opuesta + categoría DELIVERY) y descarta ambas filas antes de que lleguen al ledger. El lote original sigue vivo con su fecha/precio reales.
   - A favor: el motor FIFO no necesita saber que esto existe.
   - En contra: si el patrón de reconocimiento falla (un caso real distinto que se parezca), se pierde una fila sin dejar rastro.
2. **Guardar y etiquetar, ignorar en el cálculo** *(adoptada)*: ambas filas entran en el ledger tal cual (el recuento de filas del ledger sigue coincidiendo con el del extracto del broker, trazabilidad completa), con `tipo_movimiento = AJUSTE_TECNICO`. El motor FIFO tiene una única regla: saltar cualquier fila `AJUSTE_TECNICO`. El lote original conserva su fecha y precio de compra reales hasta la venta real.
   - A favor: trazabilidad total + coste fiscal correcto, con una sola regla explícita y visible en el ledger en vez de una decisión silenciosa del adapter.
   - En contra: una pieza más que mantener (la regla de "saltar" en el motor de cálculo), pero es trivial.
3. **Tomarlo al pie de la letra**: procesar cada fila como una venta y una compra reales. Es la opción más simple de programar y la que se descarta: reescribe la fecha de adquisición y, salvo coincidencia exacta de precio, la base de coste, de forma silenciosa.

**Decisión: opción 2.** Combina la trazabilidad de la opción 3 con la corrección fiscal de la opción 1.

## 2. Cuotas de deuda no reconocida en el ledger (préstamo/hipoteca)

El usuario ha excluido explícitamente el inmueble de "patrimonio financiero". Eso fija la regla, no es una simplificación de conveniencia:

**Regla general**: el pago de una deuda cuenta como gasto puro si y solo si el activo o pasivo que esa deuda financia no está en el balance que este sistema sigue. En cuanto ese pasivo (o el activo que garantiza) entrara en el alcance, habría que separar la cuota en interés (gasto) + amortización de principal (reduce el pasivo, no es gasto). Mientras el inmueble esté fuera, toda la cuota — "LIQUIDACION PERIODICA PRESTAMO" en el extracto bancario — es gasto, sin excepción, y la regla es la misma para cualquier banco o tipo de deuda (hipoteca, préstamo personal, financiación de coche no seguida): la pregunta a hacerse siempre es "¿el pasivo que esto amortiza está en mi ledger?", no el nombre del concepto bancario.

Esto queda anotado también como limitación del sistema en la lista de supuestos: si el usuario decide más adelante añadir el inmueble y su hipoteca al alcance, esta regla cambia para esa deuda concreta.

## 3. `Fecha operación` vs. `Fecha valor` en extractos bancarios españoles

Investigado porque afecta directamente a la reconciliación al céntimo (regla 9 de `CLAUDE.md`).

- **Fecha operación**: fecha en la que el banco registra y muestra el movimiento en el histórico — es el orden en el que aparecen las filas del extracto y con el que se calcula el saldo mostrado tras cada una.
- **Fecha valor**: fecha que determina el efecto económico/financiero del movimiento (relevante para devengo de intereses). Está regulada — el marco de servicios de pago (transposición de la normativa europea PSD2) prohíbe que un banco fije una fecha valor de adeudo anterior al momento real del cargo, y obliga a abonar los ingresos con fecha valor no posterior a su recepción — precisamente para proteger al cliente de que el banco "mueva" la fecha en su contra. Por eso se ven desfases: un pago con tarjeta reportado con `Fecha operación` unos días después de la compra real puede llevar `Fecha valor` igual al día de la compra.
- **Consecuencia práctica para el ledger**: la columna `Saldo` del extracto está calculada en el orden de `Fecha operación` (así es como el banco lista y acumula los movimientos), no en el de `Fecha valor`. Si ordenáramos el ledger por `Fecha valor`, la reconciliación fila-a-fila contra el `Saldo` declarado dejaría de cuadrar.

**Decisión**: `fecha` del ledger = `Fecha operación` (es la que permite reconciliar el saldo al céntimo). `Fecha valor` se guarda como campo adicional (`fecha_valor`), útil para cotejar contra la fecha real del cargo en el comercio o la fecha de liquidación de otro extracto relacionado.

## 4. Registro de cuentas propias (lifecycle mínimo)

Para poder distinguir un traspaso interno (no es ahorro ni gasto) de un flujo externo, todos los adapters necesitan saber qué IBANs/cuentas son del propio usuario. Se resuelve con una tabla de referencia sencilla, no con lógica de negocio dispersa en cada adapter:

`registro_cuentas`: `entidad, iban_o_cuenta, alias, fecha_alta, estado (activa/cerrada), fecha_baja`.

Cada adapter consulta esta tabla para marcar `es_flujo_externo = false` cuando la contraparte de un movimiento coincide con una fila activa del registro. Añadir o cerrar una cuenta es una fila nueva en este registro, no un cambio de código.
