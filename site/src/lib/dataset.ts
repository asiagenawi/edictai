/**
 * Dataset downloads in the formats open data is usually published in: JSON, CSV and
 * Excel. The same functions write the static files at build time and the filtered
 * files in the browser, so every format carries the same columns and values.
 */
import { strToU8, zipSync } from 'fflate';

export type Value = string | string[];
export type Row = Record<string, Value>;

export interface Dataset {
  title: string;
  columns: readonly string[];
  rows: Row[];
  /** Extra top-level fields for the JSON file, e.g. a jurisdiction. */
  meta?: Record<string, string>;
  /** How CSV and Excel join a list column; ";" unless given. */
  seps?: Record<string, string>;
}

export const FORMATS = [
  { ext: 'json', label: 'JSON', type: 'application/json' },
  { ext: 'csv', label: 'CSV', type: 'text/csv; charset=utf-8' },
  { ext: 'xlsx', label: 'Excel', type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' },
] as const;
export type Ext = (typeof FORMATS)[number]['ext'];

export const LICENSE = 'CC BY 4.0';

const flat = (v: Value | undefined, sep: string) => (Array.isArray(v) ? v.join(sep) : v ?? '');

export function toJson(d: Dataset): string {
  return JSON.stringify({ title: d.title, license: LICENSE, ...d.meta, records: d.rows }, null, 2) + '\n';
}

export function toCsv(d: Dataset): string {
  const cell = (v: string) => (/[",\n\r]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);
  const lines = [d.columns.join(','), ...d.rows.map((r) => d.columns.map((c) => cell(flat(r[c], d.seps?.[c] ?? ';'))).join(','))];
  return lines.join('\r\n') + '\r\n';
}

// A one-sheet workbook with a bold, frozen header row and text cells.
const xml = (s: string) =>
  s.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, '')
    .replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!);
const colName = (i: number) => {
  let s = '';
  for (i += 1; i > 0; i = Math.floor((i - 1) / 26)) s = String.fromCharCode(65 + ((i - 1) % 26)) + s;
  return s;
};
const XLSX_CELL_MAX = 32767;

export function toXlsx(d: Dataset): Uint8Array {
  const row = (vals: string[], r: number, style: number) =>
    `<row r="${r}">${vals.map((v, i) =>
      `<c r="${colName(i)}${r}" t="inlineStr"${style ? ` s="${style}"` : ''}><is><t xml:space="preserve">${xml(v.slice(0, XLSX_CELL_MAX))}</t></is></c>`).join('')}</row>`;
  const sheet = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><sheetData>${
    row([...d.columns], 1, 1)}${d.rows.map((r, i) => row(d.columns.map((c) => flat(r[c], d.seps?.[c] ?? '; ')), i + 2, 0)).join('')}</sheetData></worksheet>`;
  const files: Record<string, string> = {
    '[Content_Types].xml': `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>`,
    '_rels/.rels': `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>`,
    'xl/workbook.xml': `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="${xml(d.title.replace(/[\\/?*[\]:]/g, '').replace(/\s+/g, ' ').slice(0, 31).trim())}" sheetId="1" r:id="rId1"/></sheets></workbook>`,
    'xl/_rels/workbook.xml.rels': `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>`,
    'xl/styles.xml': `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>`,
    'xl/worksheets/sheet1.xml': sheet,
  };
  return zipSync(Object.fromEntries(Object.entries(files).map(([k, v]) => [k, strToU8(v)])), { level: 6 });
}

export function render(d: Dataset, ext: Ext): string | Uint8Array {
  return ext === 'json' ? toJson(d) : ext === 'csv' ? toCsv(d) : toXlsx(d);
}

/** A static endpoint's response for one format. */
export function respond(d: Dataset, ext: Ext): Response {
  const type = FORMATS.find((f) => f.ext === ext)!.type;
  return new Response(render(d, ext), { headers: { 'Content-Type': type } });
}
