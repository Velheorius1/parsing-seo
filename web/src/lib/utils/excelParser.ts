import * as XLSX from 'xlsx';

export interface ExcelData {
  sheets: string[];
  columns: Record<string, string[]>;
  rows: Record<string, string[][]>;
}

export async function parseExcel(file: File): Promise<ExcelData> {
  const buffer = await file.arrayBuffer();
  const workbook = XLSX.read(buffer, { type: 'array' });

  const sheets = workbook.SheetNames;
  const columns: Record<string, string[]> = {};
  const rows: Record<string, string[][]> = {};

  for (const name of sheets) {
    const sheet = workbook.Sheets[name];
    const json = XLSX.utils.sheet_to_json<string[]>(sheet, { header: 1, defval: '' });

    if (json.length === 0) {
      columns[name] = [];
      rows[name] = [];
      continue;
    }

    // First row as headers
    columns[name] = (json[0] || []).map((c) => String(c).trim());
    rows[name] = json.slice(1).map((row) => row.map((cell) => String(cell).trim()));
  }

  return { sheets, columns, rows };
}

export function getColumnValues(
  data: ExcelData,
  sheet: string,
  column: string,
): string[] {
  const cols = data.columns[sheet] || [];
  const colIndex = cols.indexOf(column);
  if (colIndex === -1) return [];

  const sheetRows = data.rows[sheet] || [];
  return sheetRows
    .map((row) => row[colIndex] || '')
    .filter((v) => v.length > 0);
}
