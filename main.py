from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
import pandas as pd
import io
import re

app = FastAPI(title="Reconciliation System API & Web UI", version="2.0")

def clean_currency(value):
    """
    Mengubah format teks/accounting seperti '(156.000.000)', '-156.000.000', atau '156.000.000'
    menjadi float murni (-156000000.0 atau 156000000.0).
    """
    if pd.isna(value):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    
    val_str = str(value).strip()
    if not val_str:
        return 0.0
    
    # Deteksi format negatif dengan kurung: (123.456) -> -123.456
    is_negative = False
    if val_str.startswith('(') and val_str.endswith(')'):
        is_negative = True
        val_str = val_str[1:-1]
    elif val_str.startswith('-'):
        is_negative = True
        val_str = val_str[1:]
    
    # Hapus pemisah ribuan & penanda mata uang
    val_str = re.sub(r'[^0-9,\.]', '', val_str)
    
    # Parsing pemisah ribuan/desimal
    if '.' in val_str and ',' in val_str:
        val_str = val_str.replace('.', '').replace(',', '.')
    elif '.' in val_str:
        parts = val_str.split('.')
        if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) != 2):
            val_str = val_str.replace('.', '')
    elif ',' in val_str:
        val_str = val_str.replace(',', '.')

    try:
        val = float(val_str)
        return -val if is_negative else val
    except ValueError:
        return 0.0

def process_reconciliation(df: pd.DataFrame):
    if df.shape[1] < 12:
        raise HTTPException(status_code=400, detail="File CSV tidak memiliki setidaknya 12 kolom (Kolom L).")

    col_l_name = df.columns[11]

    # Clean & Absolute Values
    df['nilai_clean'] = df[col_l_name].apply(clean_currency)
    df['abs_val'] = df['nilai_clean'].abs()

    # Siapkan kolom penanda
    df['Status_Rekonsiliasi'] = 'MATCHED'
    df['Keterangan'] = 'Memiliki Pasangan'

    # Algoritma Matching per Nilai Mutlak (1-to-1 Matching)
    for abs_val, group in df.groupby('abs_val'):
        if abs_val == 0:
            continue

        pos_indices = group[group['nilai_clean'] > 0].index.tolist()
        neg_indices = group[group['nilai_clean'] < 0].index.tolist()

        len_pos = len(pos_indices)
        len_neg = len(neg_indices)

        matched_count = min(len_pos, len_neg)

        unmatched_pos = pos_indices[matched_count:]
        unmatched_neg = neg_indices[matched_count:]

        if unmatched_pos:
            df.loc[unmatched_pos, 'Status_Rekonsiliasi'] = 'UNMATCHED_SURPLUS'
            df.loc[unmatched_pos, 'Keterangan'] = f'Kelebihan Nilai Positif (Total (+): {len_pos}, Total (-): {len_neg})'

        if unmatched_neg:
            df.loc[unmatched_neg, 'Status_Rekonsiliasi'] = 'UNMATCHED_DEFICIT'
            df.loc[unmatched_neg, 'Keterangan'] = f'Kelebihan Nilai Negatif (Total (+): {len_pos}, Total (-): {len_neg})'

    unmatched_df = df[df['Status_Rekonsiliasi'] != 'MATCHED'].copy()
    unmatched_df = unmatched_df.drop(columns=['nilai_clean', 'abs_val'])

    return {
        "total_rows": len(df),
        "total_unmatched": len(unmatched_df),
        "total_surplus_unmatched": len(df[df['Status_Rekonsiliasi'] == 'UNMATCHED_SURPLUS']),
        "total_deficit_unmatched": len(df[df['Status_Rekonsiliasi'] == 'UNMATCHED_DEFICIT']),
        "columns": list(unmatched_df.columns),
        "data": unmatched_df.fillna("").to_dict(orient='records')
    }

@app.get("/", response_class=HTMLResponse)
async def home_ui():
    """Halaman Antarmuka / Web UI Interaktif"""
    html_content = """<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Sistem Rekonsiliasi Transaksi (CSV)</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
</head>
<body class="bg-slate-50 text-slate-800 min-h-screen font-sans">
    <header class="bg-slate-900 text-white py-5 shadow-md">
        <div class="max-w-7xl mx-auto px-6 flex items-center justify-between">
            <div class="flex items-center space-x-3">
                <div class="bg-indigo-600 text-white p-2.5 rounded-xl">
                    <i class="fa-solid fa-scale-balanced text-xl"></i>
                </div>
                <div>
                    <h1 class="text-xl font-bold tracking-wide">ReconcilePro CSV</h1>
                    <p class="text-xs text-slate-400">Deteksi Otomatis Transaksi Tanpa Pasangan (Kolom L)</p>
                </div>
            </div>
            <span class="bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 text-xs px-3 py-1 rounded-full font-medium">FastAPI Engine v2.0</span>
        </div>
    </header>

    <main class="max-w-7xl mx-auto px-6 py-8">
        <!-- Hero & Upload Section -->
        <div id="uploadSection" class="bg-white rounded-2xl shadow-sm border border-slate-200 p-8 mb-8 text-center">
            <div class="max-w-xl mx-auto">
                <div class="w-16 h-16 bg-indigo-50 text-indigo-600 rounded-2xl flex items-center justify-center mx-auto mb-4 border border-indigo-100 shadow-inner">
                    <i class="fa-solid fa-file-csv text-2xl"></i>
                </div>
                <h2 class="text-2xl font-bold text-slate-900 mb-2">Unggah File CSV Rekonsiliasi</h2>
                <p class="text-sm text-slate-500 mb-6">Pilih file CSV yang akan direkonsiliasi. Sistem akan mencocokkan nilai positif & negatif pada <b>Kolom L</b> secara 1-to-1 dan menampilkan sisa yang tidak berpasangan.</p>
                
                <form id="uploadForm" class="space-y-4">
                    <div id="dropZone" class="border-2 border-dashed border-slate-300 rounded-xl p-8 transition-all hover:border-indigo-500 hover:bg-slate-50/50 cursor-pointer flex flex-col items-center justify-center">
                        <input type="file" id="csvFile" name="file" accept=".csv" class="hidden">
                        <i class="fa-solid fa-cloud-arrow-up text-3xl text-slate-400 mb-3"></i>
                        <p class="text-sm font-semibold text-slate-700" id="fileLabel">Klik untuk memilih file CSV</p>
                        <p class="text-xs text-slate-400 mt-1">Format didukung: .csv (Pemisah koma atau titik koma)</p>
                    </div>

                    <button type="submit" id="btnSubmit" disabled class="w-full bg-indigo-600 hover:bg-indigo-700 disabled:bg-slate-300 text-white font-semibold py-3 px-6 rounded-xl transition-all shadow-md flex items-center justify-center space-x-2 disabled:cursor-not-allowed">
                        <i class="fa-solid fa-bolt"></i>
                        <span>Proses & Analisis Data</span>
                    </button>
                </form>

                <div id="loading" class="hidden mt-6 flex flex-col items-center">
                    <div class="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600 mb-2"></div>
                    <span class="text-xs font-medium text-slate-600">Memproses data CSV...</span>
                </div>
            </div>
        </div>

        <!-- Results Section -->
        <div id="resultSection" class="hidden space-y-6">
            <!-- Metric Cards -->
            <div class="grid grid-cols-1 md:grid-cols-4 gap-4">
                <div class="bg-white p-5 rounded-xl border border-slate-200 shadow-sm">
                    <p class="text-xs text-slate-500 font-medium">Total Baris Diolah</p>
                    <p id="mTotal" class="text-2xl font-bold text-slate-800 mt-1">0</p>
                </div>
                <div class="bg-white p-5 rounded-xl border border-slate-200 shadow-sm">
                    <p class="text-xs text-slate-500 font-medium">Tanpa Pasangan (Unmatched)</p>
                    <p id="mUnmatched" class="text-2xl font-bold text-amber-600 mt-1">0</p>
                </div>
                <div class="bg-white p-5 rounded-xl border border-slate-200 shadow-sm">
                    <p class="text-xs text-slate-500 font-medium">Kelebihan Positif (+)</p>
                    <p id="mSurplus" class="text-2xl font-bold text-emerald-600 mt-1">0</p>
                </div>
                <div class="bg-white p-5 rounded-xl border border-slate-200 shadow-sm">
                    <p class="text-xs text-slate-500 font-medium">Kelebihan Negatif (-)</p>
                    <p id="mDeficit" class="text-2xl font-bold text-rose-600 mt-1">0</p>
                </div>
            </div>

            <!-- Table Card -->
            <div class="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
                <div class="p-6 border-b border-slate-100 flex items-center justify-between">
                    <div>
                        <h3 class="font-bold text-slate-900 text-lg">Daftar Transaksi Tanpa Pasangan</h3>
                        <p class="text-xs text-slate-500">Baris di bawah adalah transaksi yang bernilai tunggal atau melebihi kuantitas pasangannya.</p>
                    </div>
                    <button id="btnReset" class="text-xs font-semibold px-4 py-2 border border-slate-300 rounded-lg hover:bg-slate-50 text-slate-700">
                        <i class="fa-solid fa-arrow-left mr-1"></i> Upload File Lain
                    </button>
                </div>

                <div class="overflow-x-auto max-h-[550px]">
                    <table class="w-full text-left border-collapse text-xs" id="resultTable">
                        <thead class="bg-slate-100 text-slate-700 uppercase sticky top-0 font-semibold z-10">
                            <tr id="tableHeader"></tr>
                        </thead>
                        <tbody id="tableBody" class="divide-y divide-slate-100 text-slate-700"></tbody>
                    </table>
                </div>
            </div>
        </div>
    </main>

    <script>
        const dropZone = document.getElementById('dropZone');
        const csvFileInput = document.getElementById('csvFile');
        const fileLabel = document.getElementById('fileLabel');
        const btnSubmit = document.getElementById('btnSubmit');
        const uploadForm = document.getElementById('uploadForm');
        const loading = document.getElementById('loading');
        const uploadSection = document.getElementById('uploadSection');
        const resultSection = document.getElementById('resultSection');

        dropZone.addEventListener('click', () => csvFileInput.click());

        csvFileInput.addEventListener('change', (e) => {
            if (e.target.files.length > 0) {
                fileLabel.innerHTML = `File terpilih: <span class="text-indigo-600 font-bold">${e.target.files[0].name}</span>`;
                btnSubmit.disabled = false;
            }
        });

        uploadForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            if (!csvFileInput.files[0]) return;

            const formData = new FormData();
            formData.append('file', csvFileInput.files[0]);

            loading.classList.remove('hidden');
            btnSubmit.disabled = true;

            try {
                const response = await fetch('/reconcile-csv/', {
                    method: 'POST',
                    body: formData
                });

                const res = await response.json();
                loading.classList.add('hidden');

                if (!response.ok) {
                    alert(res.detail || "Terjadi kesalahan saat memproses file CSV.");
                    btnSubmit.disabled = false;
                    return;
                }

                displayResults(res);
            } catch (err) {
                loading.classList.add('hidden');
                btnSubmit.disabled = false;
                alert("Gagal menghubungkan ke server: " + err.message);
            }
        });

        function displayResults(res) {
            document.getElementById('mTotal').innerText = res.total_rows.toLocaleString('id-ID');
            document.getElementById('mUnmatched').innerText = res.total_unmatched.toLocaleString('id-ID');
            document.getElementById('mSurplus').innerText = res.total_surplus_unmatched.toLocaleString('id-ID');
            document.getElementById('mDeficit').innerText = res.total_deficit_unmatched.toLocaleString('id-ID');

            const headerTr = document.getElementById('tableHeader');
            const bodyTb = document.getElementById('tableBody');
            headerTr.innerHTML = '';
            bodyTb.innerHTML = '';

            if (res.data.length === 0) {
                bodyTb.innerHTML = `<tr><td colspan="100%" class="text-center py-8 text-emerald-600 font-medium">Semua transaksi pada kolom L saling menihilkan (100% Matched)!</td></tr>`;
            } else {
                res.columns.forEach(col => {
                    const th = document.createElement('th');
                    th.className = "py-3 px-4 border-b border-slate-200 whitespace-nowrap";
                    th.innerText = col;
                    headerTr.appendChild(th);
                });

                res.data.forEach(row => {
                    const tr = document.createElement('tr');
                    tr.className = row.Status_Rekonsiliasi === 'UNMATCHED_SURPLUS' ? 'bg-emerald-50/40 hover:bg-emerald-50' : 'bg-rose-50/40 hover:bg-rose-50';

                    res.columns.forEach(col => {
                        const td = document.createElement('td');
                        td.className = "py-2.5 px-4 whitespace-nowrap border-b border-slate-100";
                        
                        if (col === 'Status_Rekonsiliasi') {
                            const badgeColor = row[col] === 'UNMATCHED_SURPLUS' ? 'bg-emerald-100 text-emerald-800' : 'bg-rose-100 text-rose-800';
                            td.innerHTML = `<span class="px-2 py-0.5 rounded text-[10px] font-bold ${badgeColor}">${row[col]}</span>`;
                        } else if (col === 'Keterangan') {
                            td.innerHTML = `<span class="font-medium text-slate-700">${row[col]}</span>`;
                        } else {
                            td.innerText = row[col] !== null ? row[col] : '';
                        }
                        tr.appendChild(td);
                    });
                    bodyTb.appendChild(tr);
                });
            }

            uploadSection.classList.add('hidden');
            resultSection.classList.remove('hidden');
        }

        document.getElementById('btnReset').addEventListener('click', () => {
            csvFileInput.value = '';
            fileLabel.innerHTML = 'Klik untuk memilih file CSV';
            btnSubmit.disabled = true;
            resultSection.classList.add('hidden');
            uploadSection.classList.remove('hidden');
        });
    </script>
</body>
</html>"""
    return HTMLResponse(content=html_content)

@app.post("/reconcile-csv/")
async def reconcile_csv(file: UploadFile = File(...)):
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="File harus berformat CSV (.csv)")

    try:
        contents = await file.read()
        
        # Otomatisasi deteksi separator koma (,) atau titik-koma (;)
        try:
            df = pd.read_csv(io.BytesIO(contents), encoding='utf-8')
            if df.shape[1] < 5:
                df = pd.read_csv(io.BytesIO(contents), encoding='utf-8', sep=';')
        except Exception:
            df = pd.read_csv(io.BytesIO(contents), encoding='latin1', sep=None, engine='python')

        return JSONResponse(content=process_reconciliation(df))

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal memproses CSV: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
