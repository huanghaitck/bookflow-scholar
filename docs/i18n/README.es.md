# Manual de Bookflow Scholar (español)

[Descargar](https://github.com/huanghaitck/bookflow-scholar/releases/tag/v0.8.0-rc.2) · [Informar de un problema](https://github.com/huanghaitck/bookflow-scholar/issues/new?template=user_problem.yml) · [Plan hacia 1.0](../ROADMAP_1.0.md) · [Inicio](../../README.md)

## Qué hace

Bookflow Scholar es una aplicación de escritorio para Windows que traduce y reconstruye el diseño de artículos, libros y monografías. Primero recupera las unidades lógicas que atraviesan páginas, las traduce como un conjunto y vuelve a insertar `【página original】` en el límite real. El procesamiento determinista resuelve las operaciones reproducibles y los modelos multimodales ayudan con diseños y objetos visuales que las reglas no pueden clasificar de forma fiable.

Mejoras principales:

- texto principal, encabezados, pies, notas al pie y notas finales se segmentan, traducen y reconstruyen por separado;
- imágenes, mapas, figuras, leyendas y tablas se colocan según el contexto; las imágenes de copyright no relacionadas pueden excluirse;
- cambios de glosario limitados por Source, unidad de traducción y occurrence/span exactos;
- retorno no destructivo y por objeto de las páginas difíciles;
- nombres dinámicos para las ediciones original, traducida y bilingüe;
- pausa, continuación, reanudación tras reiniciar, cancelación y reintento;
- vista previa del PDF final con página anterior, siguiente y salto directo;
- chino simplificado, inglés, francés, alemán, japonés y español; se cubrieron las 30 direcciones.

## Primer uso

1. Instale `Bookflow-Scholar-0.8.0-rc.2-setup.exe`, o extraiga el ZIP portable y ejecute `Bookflow Scholar.exe`.
2. Seleccione **Create project**. El proyecto debe existir antes de importar el PDF para definir su espacio de trabajo y contexto.
3. Abra el proyecto, configure proveedores de texto y visión, modelos y claves API, y guarde. Las claves se almacenan en el Administrador de credenciales de Windows.
4. Seleccione **Import PDF** y elija los idiomas de origen y destino. En proyectos con varios Source, seleccione explícitamente el activo.
5. Seleccione **Start**. Puede pausar, continuar, cancelar o reiniciar la aplicación y reanudar el trabajo.
6. Al terminar, revise el PDF final en Overview. Use Anterior, Siguiente o `página actual/total`.
7. Los paquetes de glosario y páginas difíciles solo se generan si hay candidatos. Siga el prompt oficial incluido y vuelva a importar el paquete completado.
8. Seleccione **Open output folder** para acceder a las tres ediciones.

## Instalación y seguridad

Esta versión candidata no está firmada y Windows puede mostrar SmartScreen. Verifique el SHA-256 publicado en la Release o use el ZIP portable. [Descargue LibreOffice desde su sitio oficial](https://www.libreoffice.org/download/); es opcional, pero recomendado.

No publique documentos confidenciales, claves API, cabeceras de autorización, rutas privadas ni datos personales. Utilice el [formulario gratuito de GitHub](https://github.com/huanghaitck/bookflow-scholar/issues/new?template=user_problem.yml).
