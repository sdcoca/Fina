# CLAUDE.md — Sistema de seguimiento patrimonial y fiscal

## Cómo trabajar con el usuario

1. Preguntas siempre numeradas. Nunca lanzar más de una pregunta sin numerar; el usuario no responde si no puede referirse a ellas por número.
2. Cuando haga falta una decisión del usuario, presentar opciones concretas (A, B, C...) con una recomendación marcada explícitamente y su razón — nunca dejar el abanico abierto sin más.
3. No repetir estas normas de interacción una vez leídas; aplicarlas sin recordárselas al usuario.
4. Todas las listas van numeradas. Nunca usar bullets sin numerar.
5. Cuando una respuesta tenga varias secciones numeradas, los puntos dentro de cada sección llevan numeración jerárquica de esa sección (1.1, 1.2, 2.1...) en vez de reiniciar una numeración plana en cada bloque — así el usuario puede referirse a un punto exacto sin ambigüedad entre listas distintas de la misma respuesta.
6. No implementar (crear/editar ficheros de código, datos o configuración) hasta que el usuario lo confirme explícitamente ("hazlo", "adelante", "impleméntalo"...). Proponer el plan primero.
7. Señalar activamente cuándo una propuesta se aparta del prompt original o de cualquier ejemplo que dé el usuario (como el PDF de referencia), y explicar por qué.
8. Trabajar sección por sección / pieza por pieza cuando el usuario lo pida así, cerrando cada una (diseño + validación) antes de pasar a la siguiente.

## Estándares de calidad del proyecto (del encargo original)

9. Exactitud al céntimo en todo lo transaccional; usar aritmética decimal, nunca float, en cálculos monetarios.
10. Trazabilidad: cada cifra debe poder verificarse contra un documento fuente en menos de un minuto.
11. Separación explícita entre datos medidos y estimados, con la magnitud de incertidumbre y qué documento la resolvería.
12. Estabilidad entre revisiones: una cifra publicada no cambia salvo causa identificada y explicada.
13. Sin dobles conteos: los traspasos entre cuentas propias del usuario nunca cuentan como ahorro nuevo ni como rendimiento.
14. Multidivisa: todo resultado final en euros, coherente con la normativa fiscal española (conversión al tipo de cambio de la fecha de la operación).
15. Autocrítica: cualquier cuadre que falle debe señalarse antes de presentar el resultado, nunca absorberse en silencio.

## Interfaz visual

19. La aplicación es *mobile-first*. Cualquier verificación visual (mocks, gráficas, informe) se hace primero a un ancho de móvil real (~390px), no en escritorio — un elemento que se ve bien a 900px puede tapar toda la gráfica a 390px. Cuando algo se pueda renderizar y comprobar con un navegador antes de darlo por bueno, se hace así en vez de razonar a ciegas sobre el CSS.
20. Ningún efecto visual depende de `backdrop-filter` ni de otras características de dudoso soporte transversal; la traslucidez se consigue con transparencia simple (alfa), que funciona en cualquier motor.

## Arquitectura de datos

16. Fuente única de verdad: un master ledger cronológico, una fila por movimiento, agnóstico del activo o la entidad. Ninguna vista (por activo, por mes, fiscal...) es un fichero paralelo; todas son consultas sobre el mismo ledger.
17. Cualquier vista agregada (puente patrimonial, resultado por activo, indicadores clave...) debe cuadrar exactamente con las demás vistas derivadas del mismo ledger; las discrepancias se validan con tests automáticos, no a ojo.
18. Datos personales (nombres, IBANs, importes reales de extractos) nunca se guardan en el repositorio. Los ficheros de ejemplo/test se anonimizan por completo: nombres, IBANs, importes y conceptos, no solo los identificadores obvios.
