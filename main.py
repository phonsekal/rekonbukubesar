from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.responses import JSONResponse, HTMLResponse
import pandas as pd
import io
import re

app = FastAPI(title="Reconciliation System API & Web UI", version="6.1")

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

def format_number_clean(val: float) -> str:
    """Format angka tanpa 'Rp' dan tanpa desimal. Negatif ditulis dalam kurung (123.456)"""
    val_int = int(round(val))
    if val_int < 0:
        return f"({abs(val_int):,})".replace(",", ".")
    return f"{val_int:,}".replace(",", ".")

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

    # Header Akun
    kode_akun_header = str(df[col_kode_akun].iloc[0]) if not df.empty else "-"
    nama_akun_header = str(df[col_nama_akun].iloc[0]) if not df.empty else "-"

    # Preprocessing
    df['periode_str'] = df[col_kode_periode].astype(str).str.strip()
    df['nilai_clean'] = df[col_l_name].apply(clean_currency)
    df['abs_val'] = df['nilai_clean'].abs()

    # Sort berdasarkan Periode
    df = df.sort_values(by='periode_str').reset_index(drop=True)
    df['row_id'] = df.index

    # 1. PENCOCOKAN DALAM SKOP CAKUPAN PERIODE (Tabel Utama)
    if filter_mode == 'EXACT' and target_period:
        scope_df = df[df['periode_str'] == target_period].copy()
    elif filter_mode == 'UNTIL' and target_period:
        scope_df = df[df['periode_str'] <= target_period].copy()
    else:
        scope_df = df.copy()

    scope_df['matched_in_scope'] = False

    for abs_val, group in scope_df.groupby('abs_val'):
        if abs_val == 0:
            continue
        pos_indices = group[group['nilai_clean'] > 0].index.tolist()
        neg_indices = group[group['nilai_clean'] < 0].index.tolist()
        matched_cnt = min(len(pos_indices), len(neg_indices))

        for i in range(matched_cnt):
            scope_df.loc[pos_indices[i], 'matched_in_scope'] = True
            scope_df.loc[neg_indices[i], 'matched_in_scope'] = True

    unmatched_main = scope_df[~scope_df['matched_in_scope']].copy()

    # 2. PENCOCOKAN GLOBAL (Mencari Pasangan Penihil)
    df['matched_pair_id'] = -1
    for abs_val, group in df.groupby('abs_val'):
        if abs_val == 0:
            continue
        pos_indices = group[group['nilai_clean'] > 0].index.tolist()
        neg_indices = group[group['nilai_clean'] < 0].index.tolist()
        matched_cnt = min(len(pos_indices), len(neg_indices))

        for i in range(matched_cnt):
            p_idx = pos_indices[i]
            n_idx = neg_indices[i]
            df.loc[p_idx, 'matched_pair_id'] = n_idx
            df.loc[n_idx, 'matched_pair_id'] = p_idx

    # Kumpulkan Pasangan Lintas Periode
    resolved_pairs_list = []
    if filter_mode == 'UNTIL' and target_period:
        for idx, row in unmatched_main.iterrows():
            pair_id = df.loc[row['row_id'], 'matched_pair_id']
            if pair_id != -1:
                pair_row = df.loc[pair_id]
                if pair_row['periode_str'] > target_period:
                    resolved_pairs_list.append({
                        "Tanggal Jurnal": str(pair_row[col_tgl_jurnal]),
                        "Kode Periode Pasangan": str(pair_row[col_kode_periode]),
                        "Nomor Dokumen Pasangan": str(pair_row[col_no_doc]),
                        "Deskripsi Pasangan": str(pair_row[col_deskripsi]),
                        "target_doc": str(row[col_no_doc]),
                        "target_period": str(row['periode_str']),
                        "nilai_clean": pair_row['nilai_clean']
                    })

    # FORMATTING TABEL 1 (UTAMA)
    if not unmatched_main.empty:
        unmatched_main['Nilai'] = unmatched_main['nilai_clean'].apply(format_number_clean)
        total_main = unmatched_main['nilai_clean'].sum()

        selected_columns = [col_tgl_jurnal, col_kode_periode, col_no_doc, col_deskripsi, 'Nilai']
        main_df_final = unmatched_main[selected_columns].rename(columns={
            col_tgl_jurnal: 'Tanggal Jurnal',
            col_kode_periode: 'Kode Periode',
            col_no_doc: 'Nomor Dokumen',
            col_deskripsi: 'Deskripsi'
        })
    else:
        main_df_final = pd.DataFrame(columns=['Tanggal Jurnal', 'Kode Periode', 'Nomor Dokumen', 'Deskripsi', 'Nilai'])
        total_main = 0.0

    # FORMATTING TABEL 2 (AGREGASI / GROUP BY NOMOR DOKUMEN PASANGAN)
    if resolved_pairs_list:
        raw_res_df = pd.DataFrame(resolved_pairs_list)

        # Agregasi data jika Nomor Dokumen Pasangan sama
        aggregated_res = raw_res_df.groupby(
            ['Kode Periode Pasangan', 'Nomor Dokumen Pasangan', 'Deskripsi Pasangan'],
            as_index=False
        ).agg({
            'Tanggal Jurnal': 'first',
            'nilai_clean': 'sum',
            'target_doc': lambda x: ', '.join(sorted(set(x))),
            'target_period': 'first'
        })

        aggregated_res['Nilai'] = aggregated_res['nilai_clean'].apply(format_number_clean)
        aggregated_res['Keterangan Penyelesaian'] = aggregated_res.apply(
            lambda r: f"Pasangan Penihil Dok. {r['target_doc']} (Periode {r['target_period']})", axis=1
        )

        total_resolved = aggregated_res['nilai_clean'].sum()

        res_columns = [
            'Tanggal Jurnal',
            'Kode Periode Pasangan',
            'Nomor Dokumen Pasangan',
            'Deskripsi Pasangan',
            'Nilai',
            'Keterangan Penyelesaian'
        ]
        res_df_final = aggregated_res[res_columns]
    else:
        res_df_final = pd.DataFrame()
        total_resolved = 0.0

    return {
        "kode_akun": kode_akun_header,
        "nama_akun": nama_akun_header,
        "total_rows": len(df),
        "total_unmatched": len(main_df_final),
        "total_nilai_unmatched": format_number_clean(total_main),
        "main_columns": list(main_df_final.columns),
        "main_data": main_df_final.fillna("").to_dict(orient='records'),
        "has_resolved_later": not res_df_final.empty,
        "total_resolved_later": len(res_df_final),
        "total_nilai_resolved_later": format_number_clean(total_resolved),
        "resolved_columns": list(res_df_final.columns) if not res_df_final.empty else [],
        "resolved_data": res_df_final.fillna("").to_dict(orient='records') if not res_df_final.empty else []
    }

@app.get("/", response_class=HTMLResponse)
async def home_ui():
    html_content = """<!DOCTYPE html>
<html lang="id" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Reconciliation App — Dede Saputra</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    <style>
        body {
            background-color: #0b0f17;
            color: #e2e8f0;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        }
        .brand-cyan { color: #00d2ff; }
        .border-dark { border-color: #1e293b; }
        .bg-card { background-color: #111827; }
    </style>
</head>
<body class="min-h-screen flex flex-col justify-between">
    <!-- Navbar Header -->
    <header class="border-b border-dark py-4 px-6 sm:px-12 bg-[#0b0f17]/90 backdrop-blur sticky top-0 z-50">
        <div class="max-w-6xl mx-auto flex items-center justify-between">
            <div class="flex items-center space-x-3">
                <div class="flex space-x-1">
                    <span class="w-2.5 h-6 bg-[#00d2ff] rounded-sm transform -skew-x-12"></span>
                    <span class="w-2.5 h-6 bg-[#0a84ff] rounded-sm transform -skew-x-12"></span>
                </div>
                <span class="font-bold text-lg tracking-tight text-white">dedesaputra <span class="text-slate-400 font-normal">Reconcile</span></span>
            </div>
            <div class="text-xs text-slate-400 flex items-center space-x-4">
                <span>Projects</span>
                <span>•</span>
                <span>About</span>
            </div>
        </div>
    </header>

    <!-- Main Container -->
    <main class="max-w-6xl mx-auto px-6 py-10 w-full flex-grow">
        <!-- Title & Subtitle -->
        <div class="mb-8">
            <h1 class="text-3xl font-extrabold text-white tracking-tight mb-2">Rekonsiliasi Transaksi</h1>
            <p class="text-sm text-slate-400">Deteksi otomatis transaksi bersisa dan penggabungan pasangan penihil lintas periode.</p>
        </div>

        <!-- Upload & Options Section -->
        <div id="uploadSection" class="bg-card rounded-2xl border border-dark p-8 mb-8 shadow-xl">
            <form id="uploadForm" class="space-y-6">
                <!-- Options Filter Periode -->
                <div class="grid grid-cols-1 md:grid-cols-2 gap-4 bg-[#0b0f17] p-4 rounded-xl border border-dark">
                    <div>
                        <label class="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">
                            <i class="fa-solid fa-filter brand-cyan mr-1"></i> Mode Filter Periode
                        </label>
                        <select id="filterMode" name="filter_mode" class="w-full bg-[#111827] border border-dark text-slate-200 text-xs rounded-lg p-2.5 focus:border-[#00d2ff] outline-none">
                            <option value="ALL">Semua Periode (Tanpa Filter)</option>
                            <option value="EXACT">Hanya Periode X</option>
                            <option value="UNTIL">Sampai Dengan (s.d.) Periode X</option>
                        </select>
                    </div>

                    <div>
                        <label class="block text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">
                            <i class="fa-solid fa-calendar-days brand-cyan mr-1"></i> Pilih Periode X
                        </label>
                        <select id="targetPeriod" name="target_period" disabled class="w-full bg-[#111827] border border-dark text-slate-200 text-xs rounded-lg p-2.5 focus:border-[#00d2ff] outline-none disabled:opacity-40">
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
                <div id="dropZone" class="border border-dashed border-slate-700 rounded-xl p-10 transition-all hover:border-[#00d2ff] hover:bg-[#0b0f17]/50 cursor-pointer flex flex-col items-center justify-center text-center">
                    <input type="file" id="csvFile" name="file" accept=".csv" class="hidden">
                    <i class="fa-solid fa-cloud-arrow-up text-3xl brand-cyan mb-3"></i>
                    <p class="text-sm font-semibold text-slate-200" id="fileLabel">Unggah File CSV Rekonsiliasi</p>
                    <p class="text-xs text-slate-500 mt-1">Klik untuk memilih file atau seret file CSV ke area ini</p>
                </div>

                <button type="submit" id="btnSubmit" disabled class="w-full bg-[#0a84ff] hover:bg-[#0071e3] disabled:bg-slate-800 disabled:text-slate-600 text-white font-semibold py-3 px-6 rounded-xl transition-all shadow-lg flex items-center justify-center space-x-2 disabled:cursor-not-allowed">
                    <i class="fa-solid fa-bolt text-xs"></i>
                    <span>Proses & Analisis Data</span>
                </button>
            </form>

            <div id="loading" class="hidden mt-6 flex flex-col items-center">
                <div class="animate-spin rounded-full h-8 w-8 border-b-2 border-[#00d2ff] mb-2"></div>
                <span class="text-xs font-medium text-slate-400">Memproses data CSV...</span>
            </div>
        </div>

        <!-- Results Section -->
        <div id="resultSection" class="hidden space-y-8">
            <!-- Header Informasi Akun -->
            <div class="bg-card border border-dark rounded-xl p-6 flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
                <div>
                    <span class="text-[10px] font-bold uppercase tracking-widest text-slate-500 block mb-1">Informasi Akun</span>
                    <h2 id="displayNamaAkun" class="text-xl font-bold text-white mb-1">-</h2>
                    <p class="text-xs text-slate-400">Kode Akun: <span id="displayKodeAkun" class="font-mono brand-cyan font-semibold">-</span></p>
                </div>
                <button id="btnReset" class="text-xs font-semibold px-4 py-2 border border-dark rounded-lg hover:bg-slate-800 text-slate-300 flex items-center gap-2">
                    <i class="fa-solid fa-arrow-left"></i> Upload File Lain
                </button>
            </div>

            <!-- TABEL 1: Main Unmatched Table -->
            <div class="bg-card rounded-xl border border-dark overflow-hidden shadow-xl">
                <div class="p-5 border-b border-dark">
                    <h3 class="font-bold text-slate-200 text-sm">Daftar Transaksi Belum Memiliki Pasangan</h3>
                    <p class="text-xs text-slate-500">Nilai yang belum memiliki pasangan penihil pada kriteria periode terpilih.</p>
                </div>

                <div class="overflow-x-auto max-h-[450px]">
                    <table class="w-full text-left border-collapse text-xs">
                        <thead class="bg-[#0b0f17] text-slate-400 uppercase sticky top-0 font-semibold border-b border-dark">
                            <tr id="mainTableHeader"></tr>
                        </thead>
                        <tbody id="mainTableBody" class="divide-y divide-dark text-slate-300"></tbody>
                        <tfoot id="mainTableFooter" class="bg-[#0b0f17] font-bold border-t border-dark text-white sticky bottom-0"></tfoot>
                    </table>
                </div>
            </div>

            <!-- TABEL 2: Pasangan Penihil di Periode Selanjutnya (Digabungkan) -->
            <div id="resolvedSection" class="hidden bg-card rounded-xl border border-dark overflow-hidden shadow-xl">
                <div class="p-5 border-b border-dark flex items-center justify-between bg-indigo-950/20">
                    <div>
                        <h3 class="font-bold text-indigo-300 text-sm">Daftar Pasangan Penihil (Muncul di Periode Selanjutnya)</h3>
                        <p class="text-xs text-slate-400">Dokumen transaksi di periode selanjutnya yang menjadi pasangan penihil (Nomor dokumen sama telah dijumlahkan).</p>
                    </div>
                    <span class="bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 text-[10px] px-2.5 py-1 rounded-full font-mono">Aggregated Pairs</span>
                </div>

                <div class="overflow-x-auto max-h-[450px]">
                    <table class="w-full text-left border-collapse text-xs">
                        <thead class="bg-[#0b0f17] text-slate-400 uppercase sticky top-0 font-semibold border-b border-dark">
                            <tr id="resolvedTableHeader"></tr>
                        </thead>
                        <tbody id="resolvedTableBody" class="divide-y divide-dark text-slate-300"></tbody>
                        <tfoot id="resolvedTableFooter" class="bg-[#0b0f17] font-bold border-t border-dark text-white sticky bottom-0"></tfoot>
                    </table>
                </div>
            </div>
        </div>
    </main>

    <!-- Footer -->
    <footer class="border-t border-dark py-6 text-center text-xs text-slate-600">
        <p>Dede Saputra • © 2026 • Reconciliation System</p>
    </footer>

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
        const resolvedSection = document.getElementById('resolvedSection');

        filterMode.addEventListener('change', () => {
            if (filterMode.value === 'ALL') {
                targetPeriod.disabled = true;
                targetPeriod.classList.add('opacity-40');
            } else {
                targetPeriod.disabled = false;
                targetPeriod.classList.remove('opacity-40');
            }
        });

        dropZone.addEventListener('click', () => csvFileInput.click());

        csvFileInput.addEventListener('change', (e) => {
            if (e.target.files.length > 0) {
                fileLabel.innerHTML = `File terpilih: <span class="brand-cyan font-bold">${e.target.files[0].name}</span>`;
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
            document.getElementById('displayKodeAkun').innerText = res.kode_akun;
            document.getElementById('displayNamaAkun').innerText = res.nama_akun;

            // Render Table 1
            renderTable('mainTableHeader', 'mainTableBody', 'mainTableFooter', res.main_columns, res.main_data, res.total_nilai_unmatched);

            // Render Table 2
            if (res.has_resolved_later) {
                renderTable('resolvedTableHeader', 'resolvedTableBody', 'resolvedTableFooter', res.resolved_columns, res.resolved_data, res.total_nilai_resolved_later);
                resolvedSection.classList.remove('hidden');
            } else {
                resolvedSection.classList.add('hidden');
            }

            uploadSection.classList.add('hidden');
            resultSection.classList.remove('hidden');
        }

        function renderTable(headerId, bodyId, footerId, columns, data, totalFormatted) {
            const headerTr = document.getElementById(headerId);
            const bodyTb = document.getElementById(bodyId);
            const footerTf = document.getElementById(footerId);

            headerTr.innerHTML = '';
            bodyTb.innerHTML = '';
            footerTf.innerHTML = '';

            if (data.length === 0) {
                bodyTb.innerHTML = `<tr><td colspan="100%" class="text-center py-8 text-emerald-400 font-medium">Tidak ada data transaksi.</td></tr>`;
                return;
            }

            // Headers
            columns.forEach(col => {
                const th = document.createElement('th');
                th.className = "py-3.5 px-4 border-b border-dark whitespace-nowrap text-[11px] tracking-wider";
                th.innerText = col;
                headerTr.appendChild(th);
            });

            // Rows
            data.forEach(row => {
                const tr = document.createElement('tr');
                tr.className = 'hover:bg-slate-800/40 transition-colors';

                columns.forEach(col => {
                    const td = document.createElement('td');
                    td.className = "py-3 px-4 whitespace-nowrap border-b border-dark/50";

                    if (col === 'Nilai') {
                        td.innerHTML = `<span class="font-bold text-white font-mono">${row[col]}</span>`;
                    } else if (col === 'Kode Periode' || col === 'Kode Periode Pasangan') {
                        td.innerHTML = `<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-slate-800 text-slate-300 font-mono">${row[col]}</span>`;
                    } else if (col === 'Keterangan Penyelesaian') {
                        td.innerHTML = `<span class="px-2.5 py-1 rounded text-[10px] font-semibold bg-indigo-950/60 text-indigo-300 border border-indigo-800/50 font-mono"><i class="fa-solid fa-link text-indigo-400 mr-1"></i>${row[col]}</span>`;
                    } else {
                        td.innerText = row[col] !== null ? row[col] : '';
                    }
                    tr.appendChild(td);
                });
                bodyTb.appendChild(tr);
            });

            // Footer
            const footerTr = document.createElement('tr');
            const nilaiColIndex = columns.indexOf('Nilai');

            columns.forEach((col, idx) => {
                const td = document.createElement('td');
                td.className = "py-3.5 px-4 uppercase text-xs";

                if (idx === 0) {
                    td.innerText = "TOTAL";
                } else if (idx === nilaiColIndex) {
                    td.innerHTML = `<span class="font-mono brand-cyan font-bold text-sm">${totalFormatted}</span>`;
                } else {
                    td.innerText = "";
                }
                footerTr.appendChild(td);
            });
            footerTf.appendChild(footerTr);
        }

        document.getElementById('btnReset').addEventListener('click', () => {
            csvFileInput.value = '';
            fileLabel.innerHTML = 'Unggah File CSV Rekonsiliasi';
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
