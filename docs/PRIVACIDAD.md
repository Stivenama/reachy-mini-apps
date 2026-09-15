# Separación entre desarrollo y datos privados

El repositorio contiene software y ejemplos de configuración. Las credenciales, redes WiFi, cuentas, recuerdos del Hub y clases reales permanecen fuera de Git.

- Crear `.env` localmente; `.env.example` contiene únicamente claves vacías y ajustes genéricos.
- No añadir los respaldos del robot, carpetas de grabaciones, archivos SQLite, sesiones ni perfiles personales.
- La carpeta `environment` contiene nombres y versiones de paquetes, sin tokens ni direcciones de índices privados.
- El perfil predeterminado publicado es nuevo y genérico. Los perfiles privados se pueden recuperar del respaldo local, nunca desde este repositorio.
- El ejemplo del portal usa solo direcciones de loopback. La URL del puente MG400 se configura mediante una variable de entorno.
- El texto de prueba de lectura es sintético. No procede de las clases guardadas.

Ejecutar `python scripts/check_publication.py` y revisar `git diff --cached` antes de cada publicación. El detector es una defensa adicional y no reemplaza la revisión humana. Si alguna credencial se publica por accidente, revocarla y sustituirla: borrarla del último archivo no la elimina del historial.

Mantener este repositorio privado mientras se revisan las modificaciones y su procedencia. Los paneles del robot están pensados para una red local de confianza; publicar el código no implica exponer los puertos del robot a Internet.
