# CLAUDE.md — Sistema de seguimiento patrimonial y fiscal

## Cómo trabajar con el usuario

1. Preguntas siempre numeradas. Nunca lanzar más de una pregunta sin numerar; el usuario no responde si no puede referirse a ellas por número.
2. Cuando haga falta una decisión del usuario, presentar opciones concretas (A, B, C...) con una recomendación marcada explícitamente y su razón — nunca dejar el abanico abierto sin más.
3. No repetir estas normas de interacción una vez leídas; aplicarlas sin recordárselas al usuario.
4. Todas las listas van numeradas. Nunca usar bullets sin numerar.
5. No implementar (crear/editar ficheros de código, datos o configuración) hasta que el usuario lo confirme explícitamente ("hazlo", "adelante", "impleméntalo"...). Proponer el plan primero.
6. Señalar activamente cuándo una propuesta se aparta del prompt original o de cualquier ejemplo que dé el usuario (como el PDF de referencia), y explicar por qué.
7. Trabajar sección por sección / pieza por pieza cuando el usuario lo pida así, cerrando cada una (diseño + validación) antes de pasar a la siguiente.

## Estándares de calidad del proyecto (del encargo original)

8. Exactitud al céntimo en todo lo transaccional; usar aritmética decimal, nunca float, en cálculos monetarios.
9. Trazabilidad: cada cifra debe poder verificarse contra un documento fuente en menos de un minuto.
10. Separación explícita entre datos medidos y estimados, con la magnitud de incertidumbre y qué documento la resolvería.
11. Estabilidad entre revisiones: una cifra publicada no cambia salvo causa identificada y explicada.
12. Sin dobles conteos: los traspasos entre cuentas propias del usuario nunca cuentan como ahorro nuevo ni como rendimiento.
13. Multidivisa: todo resultado final en euros, coherente con la normativa fiscal española (conversión al tipo de cambio de la fecha de la operación).
14. Autocrítica: cualquier cuadre que falle debe señalarse antes de presentar el resultado, nunca absorberse en silencio.

## Arquitectura de datos

15. Fuente única de verdad: un master ledger cronológico, una fila por movimiento, agnóstico del activo o la entidad. Ninguna vista (por activo, por mes, fiscal...) es un fichero paralelo; todas son consultas sobre el mismo ledger.
16. Cualquier vista agregada (puente patrimonial, resultado por activo, indicadores clave...) debe cuadrar exactamente con las demás vistas derivadas del mismo ledger; las discrepancias se validan con tests automáticos, no a ojo.
