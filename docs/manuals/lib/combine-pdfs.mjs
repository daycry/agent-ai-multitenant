/**
 * Combina todos los manuales individuales (docs/manuals/pdf/NN-*.pdf) en UN
 * único PDF: docs/manuals/pdf/manual-completo.pdf, con portada e índice.
 *
 * Reutilizable: se ejecuta tras generar los manuales (`npm run combine`, o el
 * runner generate-manuals.ps1 lo invoca al final). Reúne TODO el contenido en
 * un solo documento sin volver a capturar pantallazos (fusiona los PDF ya
 * generados, así que es rápido).
 */
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { PDFDocument, StandardFonts, rgb } from "pdf-lib";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PDF_DIR = path.resolve(HERE, "..", "pdf");
const OUT = path.join(PDF_DIR, "manual-completo.pdf");
const COMBINED_NAME = "manual-completo.pdf";

function titleFromSlug(file) {
  return file
    .replace(/\.pdf$/, "")
    .replace(/^\d+-/, "")
    .replace(/-/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

// pdf-lib no tiene gradientes nativos: lo simulamos con franjas verticales
// interpolando color (indigo→violet, la firma de marca).
function drawHGradient(page, x, y, w, h, from, to, steps = 64) {
  const sw = w / steps;
  for (let i = 0; i < steps; i++) {
    const t = steps === 1 ? 0 : i / (steps - 1);
    page.drawRectangle({
      x: x + i * sw,
      y,
      width: sw + 0.7,
      height: h,
      color: rgb(
        from[0] + (to[0] - from[0]) * t,
        from[1] + (to[1] - from[1]) * t,
        from[2] + (to[2] - from[2]) * t,
      ),
    });
  }
}

// Estrella principal del icono Sparkles (lucide, viewBox 24) para el logo.
const SPARKLE_PATH =
  "M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .962 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.962 0z";

async function main() {
  if (!fs.existsSync(PDF_DIR)) {
    console.error("No existe docs/manuals/pdf — genera primero los manuales.");
    process.exit(1);
  }
  const files = fs
    .readdirSync(PDF_DIR)
    .filter((f) => f.endsWith(".pdf") && f !== COMBINED_NAME)
    .sort();
  if (files.length === 0) {
    console.error("No hay PDFs de manuales que combinar.");
    process.exit(1);
  }

  const out = await PDFDocument.create();
  const font = await out.embedFont(StandardFonts.HelveticaBold);
  const fontReg = await out.embedFont(StandardFonts.Helvetica);
  const INDIGO = [0.388, 0.4, 0.945]; // #6366f1
  const VIOLET = [0.545, 0.361, 0.965]; // #8b5cf6
  const violet = rgb(0.486, 0.227, 0.929);
  const ink = rgb(0.06, 0.09, 0.16);
  const muted = rgb(0.39, 0.45, 0.55);
  const line = rgb(0.886, 0.91, 0.941);
  const W = 595.28;
  const H = 841.89;

  // --- Portada ---
  const cover = out.addPage([W, H]);
  // Banda superior con gradiente de marca + logo + wordmark (texto blanco).
  const bandH = 176;
  drawHGradient(cover, 0, H - bandH, W, bandH, INDIGO, VIOLET);
  // Logo: estrella Sparkles blanca (lucide, escalada ~2x).
  cover.drawSvgPath(SPARKLE_PATH, {
    x: 52,
    y: H - 52,
    scale: 1.9,
    color: rgb(1, 1, 1),
  });
  cover.drawText("Agentic Platform", { x: 100, y: H - 60, size: 24, font, color: rgb(1, 1, 1) });
  cover.drawText("PLATAFORMA AGÉNTICA MULTI-TENANT", {
    x: 101,
    y: H - 78,
    size: 9,
    font,
    color: rgb(0.93, 0.92, 1),
  });
  cover.drawText("Documentación de usuario · Edición completa", {
    x: 52,
    y: H - bandH + 26,
    size: 12,
    font: fontReg,
    color: rgb(0.95, 0.94, 1),
  });

  // Cuerpo de la portada (área blanca).
  cover.drawText("MANUAL DE USUARIO", { x: 52, y: H - bandH - 56, size: 12, font, color: violet });
  cover.drawText("Guía completa", { x: 50, y: H - bandH - 104, size: 38, font, color: ink });
  cover.drawText("de la plataforma", { x: 50, y: H - bandH - 146, size: 38, font, color: ink });
  drawHGradient(cover, 52, H - bandH - 168, 64, 3, INDIGO, VIOLET);
  cover.drawText(
    `Generado: ${new Date().toISOString().slice(0, 10)}   ·   Español   ·   Versión Producción`,
    {
      x: 52,
      y: H - bandH - 190,
      size: 10.5,
      font: fontReg,
      color: muted,
    },
  );

  cover.drawText("Contenido", { x: 52, y: H - bandH - 232, size: 16, font, color: ink });
  let y = H - bandH - 260;
  files.forEach((f, i) => {
    const order = (f.match(/^(\d+)/) || ["", String(i)])[1];
    cover.drawText(order.padStart(2, "0"), { x: 54, y, size: 11, font, color: violet });
    cover.drawText(titleFromSlug(f), { x: 84, y, size: 11, font: fontReg, color: ink });
    cover.drawLine({
      start: { x: 52, y: y - 7 },
      end: { x: W - 52, y: y - 7 },
      thickness: 0.5,
      color: line,
    });
    y -= 22;
  });

  // Franja inferior de confidencialidad.
  drawHGradient(cover, 0, 0, W, 34, INDIGO, VIOLET);
  cover.drawText("CONFIDENCIAL · COMITÉ DE DIRECCIÓN", {
    x: 52,
    y: 12,
    size: 9.5,
    font,
    color: rgb(1, 1, 1),
  });
  cover.drawText("Agentic Platform", {
    x: W - 150,
    y: 12,
    size: 9.5,
    font,
    color: rgb(0.93, 0.92, 1),
  });

  // --- Concatenar cada manual ---
  for (const f of files) {
    const bytes = fs.readFileSync(path.join(PDF_DIR, f));
    const src = await PDFDocument.load(bytes);
    const pages = await out.copyPages(src, src.getPageIndices());
    pages.forEach((p) => out.addPage(p));
    console.log("  + añadido", f, `(${src.getPageCount()} págs.)`);
  }

  // --- Paginación CONTINUA del documento combinado -------------------------
  // Las páginas vienen de los PDFs individuales con su pie "pág X/Y" POR MANUAL
  // horneado por Chromium. Lo tapamos con una franja blanca (cae dentro del
  // margen inferior de 17 mm, sin contenido) y reescribimos un pie CONTINUO
  // "página N / TOTAL" sobre todo el documento. La portada (índice 0) se
  // respeta (tiene su propio diseño).
  const allPages = out.getPages();
  const contentTotal = allPages.length - 1; // excluye la portada
  for (let i = 1; i < allPages.length; i++) {
    const p = allPages[i];
    const pw = p.getSize().width;
    p.drawRectangle({ x: 0, y: 0, width: pw, height: 46, color: rgb(1, 1, 1) });
    p.drawLine({
      start: { x: 42, y: 31 },
      end: { x: pw - 42, y: 31 },
      thickness: 0.5,
      color: line,
    });
    p.drawText("Plataforma Agéntica Multi-Tenant · Manual de usuario completo", {
      x: 42,
      y: 18,
      size: 7.5,
      font: fontReg,
      color: muted,
    });
    const label = `página ${i} / ${contentTotal}`;
    const lw = fontReg.widthOfTextAtSize(label, 7.5);
    p.drawText(label, { x: pw - 42 - lw, y: 18, size: 7.5, font: fontReg, color: muted });
  }

  const bytes = await out.save();
  fs.writeFileSync(OUT, bytes);
  console.log(`\nManual completo: ${OUT} (${out.getPageCount()} págs., ${files.length} manuales)`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
