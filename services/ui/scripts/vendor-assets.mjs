import fs from "node:fs";
import path from "node:path";

function copyIfExists(src, dest) {
  if (!fs.existsSync(src)) return;
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.copyFileSync(src, dest);
}

const root = process.cwd();
const nm = (...parts) => path.join(root, "node_modules", ...parts);
const out = (...parts) => path.join(root, "public", "vendor", ...parts);

// Bootstrap
copyIfExists(nm("bootstrap", "dist", "css", "bootstrap.min.css"), out("bootstrap", "bootstrap.min.css"));
copyIfExists(nm("bootstrap", "dist", "css", "bootstrap.min.css.map"), out("bootstrap", "bootstrap.min.css.map"));
copyIfExists(nm("bootstrap", "dist", "js", "bootstrap.bundle.min.js"), out("bootstrap", "bootstrap.bundle.min.js"));
copyIfExists(nm("bootstrap", "dist", "js", "bootstrap.bundle.min.js.map"), out("bootstrap", "bootstrap.bundle.min.js.map"));

// Bootstrap Icons
copyIfExists(nm("bootstrap-icons", "font", "bootstrap-icons.min.css"), out("bootstrap-icons", "bootstrap-icons.min.css"));
copyIfExists(nm("bootstrap-icons", "font", "fonts", "bootstrap-icons.woff"), out("bootstrap-icons", "fonts", "bootstrap-icons.woff"));
copyIfExists(nm("bootstrap-icons", "font", "fonts", "bootstrap-icons.woff2"), out("bootstrap-icons", "fonts", "bootstrap-icons.woff2"));

// D3
copyIfExists(nm("d3", "dist", "d3.min.js"), out("d3", "d3.min.js"));
copyIfExists(nm("d3", "dist", "d3.min.js.map"), out("d3", "d3.min.js.map"));

// html2canvas
copyIfExists(nm("html2canvas", "dist", "html2canvas.min.js"), out("html2canvas", "html2canvas.min.js"));
copyIfExists(nm("html2canvas", "dist", "html2canvas.min.js.map"), out("html2canvas", "html2canvas.min.js.map"));

// MOSP design system
copyIfExists(nm("@FERCBusters", "mosp-design-system", "dist", "styles.css"), out("mosp-design-system", "styles.css"));
copyIfExists(nm("@FERCBusters", "mosp-design-system", "dist", "app.js"), out("mosp-design-system", "app.js"));
copyIfExists(nm("@FERCBusters", "mosp-design-system", "dist", "BUILD_INFO.txt"), out("mosp-design-system", "BUILD_INFO.txt"));

function copyRequired(src, dest) {
  if (!fs.existsSync(src)) {
    throw new Error(`Required vendored asset is missing: ${src}. Run npm install in services/ui first.`);
  }
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.copyFileSync(src, dest);
}

// Wysi rich-text editor
copyRequired(nm("wysi", "dist", "wysi.min.css"), out("wysi", "wysi.min.css"));
copyRequired(nm("wysi", "dist", "wysi.min.js"), out("wysi", "wysi.min.js"));

console.log("Vendored assets into public/vendor");
