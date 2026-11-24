# 📨 Notificador DGRDT

> ## 🤖 Modo automático (agente de escritorio)
> Con el acceso directo **«Notificador DGRDT»** del escritorio se abre una ventana de Chrome propia con la
> web del notificador. Ahí, después de generar la notificación, el botón **🚀 Notificar automáticamente** hace todo solo:
> 1. **Outlook web**: envía el mail desde `relaciones.laborales@buenosaires.gob.ar`, con las negritas, tu firma de texto y los PDF que dicen «Se adjunta…».
> 2. **PDF del enviado**: lo busca en «Elementos enviados» y lo imprime a PDF (mismo formato que Ctrl+P).
> 3. **GEDO**: Inicio de Documento → `NOTIF` → referencia `NOTIF. FEHAC. …` → importa el PDF → «Firmar con Certificado» → descarga.
> 4. **Expediente (SADE)**: busca el EX → Tramitar (o Adquirir) → vincula el IF/PV notificado y la notificación →
>    Realizar Pase (motivo estándar, Tramitación, repartición y sector configurables: por defecto DGRDT / COLECTIVA).
> 5. **Agenda**: fila en la planilla de audiencias (Google Sheets) y renglón en el Word de seguimiento (bajo el día de hoy).
>
> Los links de la planilla y del Word, la firma y el correo de prueba se cargan en **⚙️ Ajustes del agente**: quedan solo en
> la PC (`C:\Users\<usuario>\NotificadorDGRDT\config.json`), nunca en este repositorio.
>
> Antes de enviar, la web **controla** lo que escribió la IA contra los PDF (emails, números, fechas, horas, link de Meet,
> adjuntos) y **frena** si falta algo. Cada caso queda en `C:\Users\<usuario>\NotificadorDGRDT\casos\`.
>
> **Primera vez**: doble clic en `agente\INICIAR-NOTIFICADOR.bat` (instala todo y crea el acceso directo). En la ventana del
> notificador iniciá sesión una vez en Outlook, GEDO y Google. **Modo prueba** (⚙️ Ajustes del agente): el mail va solo a tu
> casilla, GEDO se saltea y la agenda se escribe en copias de prueba.
>
> Archivos: `agente/agente.py` (servidor local 127.0.0.1:8765) · `outlook.py` · `gedo.py` · `agenda_google.py` ·
> `trabajos.py` (cola de pasos) · `navegador.py` (Chrome propio, puerto 9333). La carpeta `agente/` no se publica en Vercel.

Herramienta simple para generar las notificaciones por email de la **Dirección General de Relaciones del Trabajo (DGRDT)** del Gobierno de la Ciudad de Buenos Aires (GCABA).

**Subís la Providencia (PV) y la presentación (RE) en PDF → una IA los lee, detecta el escenario y te arma la notificación lista para Outlook** (asunto + destinatarios + cuerpo). Igual que cuando los pasás por el chat, pero como programa propio.

> Usa la IA **gratuita** de Google (Gemini). No requiere instalar nada ni saber programar.

---

## 🚀 Puesta en marcha (una sola vez, ~2 minutos)

### 1) Conseguir la clave gratis de Gemini
1. Entrá a **https://aistudio.google.com/apikey**
2. Iniciá sesión con tu cuenta de Google.
3. Tocá **“Create API key”** (Crear clave) y **copiala**.

> Es gratis. La capa gratuita alcanza de sobra para el uso diario de la oficina.

### 2) Cargar la clave en la app
1. Abrí **`index.html`** (doble clic → se abre en el navegador).
2. Abajo, en **⚙️ Clave de IA (Google Gemini)**, pegá la clave y tocá **“Guardar clave”**.

Listo. La clave queda guardada en tu navegador; no hay que volver a cargarla.

---

## 📝 Uso diario

1. Arrastrá a la app la **PV** y, si existe, el **RE** (podés soltar los dos juntos).
2. Tocá **“✨ Generar notificación”**.
3. En unos segundos aparece la notificación. Copiá con los botones **📋**:
   - **Destinatarios** → al campo *Para:* de Outlook
   - **Asunto** → al *Asunto*
   - **Cuerpo** → al cuerpo del mail
   (o **“Copiar todo”** de una).
4. Si algo quedó para ajustar, tenés dos formas:
   - **Editarlo directo** en pantalla antes de copiar, o
   - **Pedírselo a la IA por el chat** (abajo del resultado): escribí por ejemplo *"notificá solo a la empresa"*, *"la audiencia es virtual"* o *"sacá ese párrafo"* y lo rehace al instante, manteniendo el contexto de los documentos.

### 💬 Correcciones por chat (solo del caso actual)

Debajo del resultado hay un chat para ajustar **la notificación que estás viendo**: escribí por ejemplo *“notificá solo a la empresa”* o *“la audiencia es virtual”* y la rehace al instante. Estas correcciones aplican **solo a esa notificación** y no cambian nada permanente ni afectan a otros usuarios.

### 📐 Reglas permanentes (seguras)

Los formatos y reglas que valen siempre (los 9 escenarios, el espaciado, la incomparecencia, etc.) están **fijos en el programa**: iguales para todo el equipo y a salvo de cambios accidentales. Si aparece una regla nueva que debe aplicarse siempre, se actualiza el programa y se vuelve a publicar (no se editan desde la web, justamente por seguridad).

---

La IA combina todos los PDF: usa la **PV** para el escenario, las fechas y la modalidad, y el **RE** para los **datos de la empresa y los emails**. Reconoce los 9 escenarios (audiencia presencial/virtual con o sin presentación, cambio de fecha con o sin presentación, reserva de acta, nueva audiencia desde acta y resolución).

Si falta algún dato en los documentos, la IA lo marca con «FALTA: …» y lo aclara en una nota, en vez de inventarlo.

---

## 🔒 Privacidad

- Para leer los PDF, la app los envía a la **IA de Google (Gemini)**, igual que cuando subís un archivo a un chat de IA.
- La clave y los archivos **no se guardan** en ningún servidor propio: todo pasa entre tu navegador y Google.
- Si manejás documentación que **no puede salir de la oficina**, consultá antes con tu área de sistemas.

---

## ☁️ (Opcional) Publicarlo para el equipo con Vercel

Para que otros lo abran desde una URL en vez de un archivo:

1. Subí esta carpeta a un repositorio de GitHub.
2. En [vercel.com](https://vercel.com) → **Import Project** → elegí el repo → **Deploy** (detecta el `vercel.json`, deploy estático sin build).
3. Compartí la URL. Cada persona carga su propia clave de Gemini la primera vez.

```
├── index.html   ← toda la app (una sola pantalla)
├── vercel.json  ← deploy estático opcional
└── README.md
```

---

## ❓ Si algo no anda

- **“La clave de Gemini no es válida”** → revisá que la pegaste completa en ⚙️ (empieza con `AIza...`).
- **“Se alcanzó el límite gratuito”** → esperá unos minutos y reintentá.
- **No genera / error de red** → fijate que tengas internet y que la red de la oficina no bloquee `googleapis.com`.
- **Un PDF muy pesado** → si es muy grande, probá subir solo las páginas necesarias.
