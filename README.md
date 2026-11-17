# Arena de Comunicación Asertiva

Concurso interactivo para un taller laboral. Los participantes entran desde su celular, eligen avatar, contestan seis situaciones y compiten en un ranking individual proyectado en pantalla.

## Uso
1. Abrir la URL del servicio.
2. Pulsar **Soy facilitadora** y crear sala.
3. Proyectar el código QR. Los trabajadores ingresan nombre y avatar.
4. Iniciar las seis rondas, revisar la explicación de cada respuesta y finalizar con el podio.

## Privacidad y disponibilidad
- Los nombres, avatares y resultados se almacenan solo en memoria mientras la instancia esté en ejecución.
- No requiere contraseñas personales ni recopila correos.
- Un reinicio o suspensión del servicio gratuito borra la partida.
- Evitar introducir información privada de otras personas.

## Despliegue
Render: servicio web Python (Free), `pip install -r requirements.txt`, `uvicorn server:app --host 0.0.0.0 --port $PORT --proxy-headers`.

