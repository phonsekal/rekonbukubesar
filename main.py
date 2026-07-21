from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.responses import JSONResponse, HTMLResponse
import pandas as pd
import io
import re

app = FastAPI(title="Reconciliation System API & Web UI", version="3.0")

def clean_currency(value):
    if pd.isna(value):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    
    val_str = str(value).strip()
    if not val_str:
        return 0.0
    
    is_negative = False
    if val_str.startswith('(') and val_str.endswith(')'):
        is_negative = True
        val_str = val_str[1:-1]
    elif val_str.startswith('-'):
        is_negative = True
        val_str = val_str[1:]
    
    val_str = re.sub(r'[^0-9,\.]', '', val_str)
    
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

def format_rupiah(val: float) -> str:
    if val < 0:
        return f"Rp ({abs(val):,.2f})".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"Rp {val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def process_reconciliation(df: pd.DataFrame, filter_mode: str = 'ALL', target_period: str = ''):
    if df.shape[1] < 12:
        raise HTTPException(status_code=400, detail="File CSV tidak memiliki setidaknya 12 kolom (s.d. Kolom L).")

    # Kolom berdasarkan urutan standar spreadsheet:
    # C(2): Kode Akun, D(3): Nama Akun, G(6): Tanggal Jurnal, H(7): Kode Periode, I(8): Nomor Dokumen, J(9): Deskripsi, L(11): Nilai
    col_kode_akun = df.columns[2]
    col_nama_akun = df.columns[3]
    col_tgl_jurnal = df.columns[6]
    col_kode_periode = df.columns[7]
    col_no_doc = df.columns[8]
    col_deskripsi = df.columns[9]
    col_l_name = df.columns[11]

    # Format Kode Periode sebagai String
    df['periode_str'] = df[col_kode_periode].astype(str).str.strip()

    # Filter Periode sebelum Rekonsiliasi
    if target_period and filter_mode != 'ALL':
        if filter_mode == 'EXACT':
            df = df[df['periode_str'] == target_period].copy()
        elif filter_mode == 'UNTIL':
            df = df[df['periode_str'] <= target_period].copy()

    if df.empty:
        return {
            "total_rows": 0,
            "total_unmatched": 0,
            "total_surplus_unmatched": 0,
            "total_deficit_unmatched": 0,
            "columns": ['Kode Akun', 'Nama Akun', 'Tanggal Jurnal', 'Kode Periode', 'Nomor Dokumen', 'Deskripsi', 'Nilai (Rupiah)', 'Status', 'Keterangan Detail'],
            "data": []
        }

    # Clean & Absolute Values
    df['nilai_clean'] = df[col_l_name].apply(clean_currency)
    df['abs_val'] = df['nilai_clean'].abs()

    # Penanda awal
    df['Status_Rekonsiliasi'] = 'MATCHED'
    df['Keterangan'] = 'Memiliki Pasangan'

    # Algoritma Matching 1-to-1
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
    unmatched_df['Nilai (Rupiah)'] = unmatched_df['nilai_clean'].apply(format_rupiah)

    selected_columns = [
        col_kode_akun,
        col_nama_akun,
        col_tgl_jurnal,
        col_kode_periode,
        col_no_doc,
        col_deskripsi,
        'Nilai (Rupiah)',
        'Status_Rekonsiliasi',
        'Keterangan'
    ]

    final_df = unmatched_df[selected_columns].copy()

    final_df = final_df.rename(columns={
        col_kode_akun: 'Kode Akun',
        col_nama_akun: 'Nama Akun',
        col_tgl_jurnal: 'Tanggal Jurnal',
        col_kode_periode: 'Kode Periode',
        col_no_doc: 'Nomor Dokumen',
        col_deskripsi: 'Deskripsi',
        'Status_Rekonsiliasi': 'Status',
        'Keterangan': 'Keterangan Detail'
    })

    return {
        "total_rows": len(df),
        "total_unmatched": len(final_df),
        "total_surplus_unmatched": len(df[df['Status_Rekonsiliasi'] == 'UNMATCHED_SURPLUS']),
        "total_deficit_unmatched": len(df[df['Status_Rekonsiliasi'] == 'UNMATCHED_DEFICIT']),
        "columns": list(final_df.columns),
        "data": final_df.fillna("").to_dict(orient='records')
    }

@app.get("/", response_class=HTMLResponse)
async def home_ui():
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
    <header class="bg-slate-900 text-white py-6 shadow-md border-b border-slate-800">
        <div class="max-w-7xl mx-auto px-6 flex items-center justify-between">
            <div class="flex items-center space-x-3">
                <div class="bg-indigo-600 text-white p-2.5 rounded-xl shadow-md">
                    <i class="fa-solid fa-scale-balanced text-xl"></i>
                </div>
                <div>
                    <h1 class="text-xl font-bold tracking-wide">ReconcilePro CSV</h1>
                    <p class="text-xs text-slate-400">Deteksi Transaksi Tanpa Pasangan dengan Filter Periode</p>
                </div>
            </div>
            <span class="bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 text-xs px-3 py-1 rounded-full font-medium">FastAPI Engine v3.0</span>
        </div>
    </header>

    <main class="max-w-7xl mx-auto px-6 py-8">
        <!-- Hero & Upload Section -->
        <div id="uploadSection" class="bg-white rounded-2xl shadow-sm border border-slate-200 p-8 mb-8">
            <div class="max-w-2xl mx-auto">
                <div class="text-center mb-6">
                    <div class="w-16 h-16 bg-indigo-50 text-indigo-600 rounded-2xl flex items-center justify-center mx-auto mb-4 border border-indigo-100 shadow-inner">
                        <i class="fa-solid fa-file-csv text-2xl"></i>
                    </div>
                    <h2 class="text-2xl font-bold text-slate-900 mb-2">Unggah File CSV Rekonsiliasi</h2>
                    <p class="text-sm text-slate-500">Pilih opsi filter periode (Opsional) sebelum mengunggah dan menganalisis data.</p>
                </div>
                
                <form id="uploadForm" class="space-y-6">
                    <!-- Options Filter Periode -->
                    <div class="bg-slate-50 p-5 rounded-xl border border-slate-200 grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div>
                            <label class="block text-xs font-bold text-slate-700 uppercase tracking-wider mb-2">
                                <i class="fa-solid fa-filter text-indigo-500 mr-1"></i> Mode Filter Periode
                            </label>
                            <select id="filterMode" name="filter_mode" class="w-full bg-white border border-slate-300 text-slate-800 text-xs rounded-lg p-2.5 focus:ring-indigo-500 focus:border-indigo-500 font-medium">
                                <option value="ALL">Semua Periode (Tanpa Filter)</option>
                                <option value="EXACT">Hanya Periode X</option>
                                <option value="UNTIL">Sampai Dengan (s.d.) Periode X</option>
                            </select>
                        </div>

                        <div>
                            <label class="block text-xs font-bold text-slate-700 uppercase tracking-wider mb-2">
                                <i class="fa-solid fa-calendar-days text-indigo-500 mr-1"></i> Pilih Periode X
                            </label>
                            <select id="targetPeriod" name="target_period" disabled class="w-full bg-slate-100 border border-slate-300 text-slate-800 text-xs rounded-lg p-2.5 focus:ring-indigo-500 focus:border-indigo-500 font-medium disabled:opacity-50">
                                <option value="">-- Pilih Periode --</option>
                                <option value="2026-01">2026-01</option>
                                <option value="2026-02">2026-02</option>
                                <option value="2026-03">2026-03</option>
                                <option value="2026-04">2026-04</option>
                                <option value="2026-05">2026-05</option>
                                <option value="2026-06">2026-06</option>
                                <option value="2026-07">2026-07</option>
                                <option value="2026-08">2026-08</option>
                                <option value="2026-09">2026-09</option>
                                <option value="2026-10">2026-10</option>
                                <option value="2026-11">2026-11</option>
                                <option value="2026-12">2026-12</option>
                            </select>
                        </div>
                    </div>

                    <!-- Drop Zone -->
                    <div id="dropZone" class="border-2 border-dashed border-slate-300 rounded-xl p-8 transition-all hover:border-indigo-500 hover:bg-slate-50/50 cursor-pointer flex flex-col items-center justify-center">
                        <input type="file" id="csvFile" name="file" accept=".csv" class="hidden">
                        <i class="fa-solid fa-cloud-arrow-up text-3xl text-slate-400 mb-3"></i>
                        <p class="text-sm font-semibold text-slate-700" id="fileLabel">Klik untuk memilih file CSV atau seret ke sini</p>
                        <p class="text-xs text-slate-400 mt-1">Format didukung: .csv (Pemisah koma atau titik koma)</p>
                    </div>

                    <button type="submit" id="btnSubmit" disabled class="w-full bg-indigo-600 hover:bg-indigo-700 disabled:bg-slate-300 text-white font-semibold py-3 px-6 rounded-xl transition-all shadow-md hover:shadow-indigo-200 flex items-center justify-center space-x-2 disabled:cursor-not-allowed">
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
                <div class="p-6 border-b border-slate-100 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                    <div>
                        <h3 class="font-bold text-slate-900 text-lg">Hasil Rekonsiliasi Transaksi Tanpa Pasangan</h3>
                        <p class="text-xs text-slate-500" id="filterSummary">Menampilkan Ringkasan Kolom Utama Termasuk Kode Periode (Kolom H).</p>
                    </div>
                    <div class="flex items-center gap-3">
                        <button id="btnReset" class="text-xs font-semibold px-4 py-2 border border-slate-300 rounded-lg hover:bg-slate-50 text-slate-700">
                            <i class="fa-solid fa-arrow-left mr-1"></i> Upload File Lain
                        </button>
                    </div>
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
        const filterMode = document.getElementById('filterMode');
        const targetPeriod = document.getElementById('targetPeriod');

        filterMode.addEventListener('change', () => {
            if (filterMode.value === 'ALL') {
                targetPeriod.disabled = true;
                targetPeriod.classList.add('bg-slate-100');
                targetPeriod.classList.remove('bg-white');
            } else {
                targetPeriod.disabled = false;
                targetPeriod.classList.remove('bg-slate-100');
                targetPeriod.classList.add('bg-white');
            }
        });

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

            if (filterMode.value !== 'ALL' && !targetPeriod.value) {
                alert("Silakan pilih Periode X terlebih dahulu!");
                return;
            }

            const formData = new FormData();
            formData.append('file', csvFileInput.files[0]);
            formData.append('filter_mode', filterMode.value);
            formData.append('target_period', targetPeriod.value);

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
                bodyTb.innerHTML = `<tr><td colspan="100%" class="text-center py-8 text-emerald-600 font-medium">Tidak ada transaksi tanpa pasangan yang ditemukan untuk kriteria ini.</td></tr>`;
            } else {
                res.columns.forEach(col => {
                    const th = document.createElement('th');
                    th.className = "py-3 px-4 border-b border-slate-200 whitespace-nowrap";
                    th.innerText = col;
                    headerTr.appendChild(th);
                });

                res.data.forEach(row => {
                    const tr = document.createElement('tr');
                    tr.className = row.Status === 'UNMATCHED_SURPLUS' ? 'bg-emerald-50/40 hover:bg-emerald-50' : 'bg-rose-50/40 hover:bg-rose-50';

                    res.columns.forEach(col => {
                        const td = document.createElement('td');
                        td.className = "py-2.5 px-4 whitespace-nowrap border-b border-slate-100";
                        
                        if (col === 'Status') {
                            const badgeColor = row[col] === 'UNMATCHED_SURPLUS' ? 'bg-emerald-100 text-emerald-800' : 'bg-rose-100 text-rose-800';
                            td.innerHTML = `<span class="px-2 py-0.5 rounded text-[10px] font-bold ${badgeColor}">${row[col]}</span>`;
                        } else if (col === 'Nilai (Rupiah)') {
                            td.innerHTML = `<span class="font-bold text-slate-900 font-mono">${row[col]}</span>`;
                        } else if (col === 'Kode Periode') {
                            td.innerHTML = `<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-slate-200 text-slate-800 font-mono">${row[col]}</span>`;
                        } else if (col === 'Keterangan Detail') {
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
            fileLabel.innerHTML = 'Klik untuk memilih file CSV atau seret ke sini';
            btnSubmit.disabled = true;
            resultSection.classList.add('hidden');
            uploadSection.classList.remove('hidden');
        });
    </script>
</body>
</html>"""
    return HTMLResponse(content=html_content)

@app.post("/reconcile-csv/")
async def reconcile_csv(
    file: UploadFile = File(...),
    filter_mode: str = Form("ALL"),
    target_period: str = Form("")
):
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="File harus berformat CSV (.csv)")

    try:
        contents = await file.read()
        
        try:
            df = pd.read_csv(io.BytesIO(contents), encoding='utf-8')
            if df.shape[1] < 5:
                df = pd.read_csv(io.BytesIO(contents), encoding='utf-8', sep=';')
        except Exception:
            df = pd.read_csv(io.BytesIO(contents), encoding='latin1', sep=None, engine='python')

        return JSONResponse(content=process_reconciliation(df, filter_mode, target_period))

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal memproses CSV: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
