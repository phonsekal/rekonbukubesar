from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
import pandas as pd
import io
import re

app = FastAPI(title="Reconciliation API", version="1.0")

def clean_currency(value):
    """
    Mengubah format teks/accounting Excel seperti '(156.000.000)' atau '156.000.000'
    menjadi float murni (-156000000.0 atau 156000000.0).
    """
    if pd.isna(value):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    
    val_str = str(value).strip()
    
    # Deteksi format negatif dengan kurung: (123.456) -> -123.456
    is_negative = False
    if val_str.startswith('(') and val_str.endswith(')'):
        is_negative = True
        val_str = val_str[1:-1]
    
    # Hapus titik ribuan (khas Indonesia) dan spasi
    val_str = val_str.replace('.', '').replace(',', '.').strip()
    
    try:
        val = float(val_str)
        return -val if is_negative else val
    except ValueError:
        return 0.0

@app.post("/reconcile-excel/")
async def reconcile_excel(file: UploadFile = File(...)):
    if not file.filename.endswith(('.xlsx', '.xls')):
        raise HTTPException(status_code=400, detail="File harus berupa spreadsheet Excel (.xlsx / .xls)")

    try:
        # Read Excel File
        contents = await file.read()
        df = pd.read_excel(io.BytesIO(contents))

        # Validasi Kolom L (Kolom ke-12 / Index 11)
        if df.shape[1] < 12:
            raise HTTPException(status_code=400, detail="File Excel tidak memiliki setidaknya 12 kolom (Kolom L).")

        # Ambil nama kolom L
        col_l_name = df.columns[11]

        # Parsing nilai numerik
        df['nilai_clean'] = df[col_l_name].apply(clean_currency)
        df['abs_val'] = df['nilai_clean'].abs()

        # Siapkan kolom penanda status
        df['Status_Rekonsiliasi'] = 'MATCHED'
        df['Keterangan'] = 'Memiliki Pasangan'

        # Algoritma Matching per Nilai Mutlak
        for abs_val, group in df.groupby('abs_val'):
            if abs_val == 0:
                continue

            # Pisahkan indeks data positif dan negatif
            pos_indices = group[group['nilai_clean'] > 0].index.tolist()
            neg_indices = group[group['nilai_clean'] < 0].index.tolist()

            len_pos = len(pos_indices)
            len_neg = len(neg_indices)

            # Tentukan berapa banyak pasangan yang berhasil dipasangkan (1-to-1)
            matched_count = min(len_pos, len_neg)

            # Sisa data positif yang tidak dapat pasangan
            unmatched_pos = pos_indices[matched_count:]
            # Sisa data negatif yang tidak dapat pasangan
            unmatched_neg = neg_indices[matched_count:]

            # Tandai sisa positif
            if unmatched_pos:
                df.loc[unmatched_pos, 'Status_Rekonsiliasi'] = 'UNMATCHED_SURPLUS'
                df.loc[unmatched_pos, 'Keterangan'] = f'Kelebihan Nilai Positif (Total +: {len_pos}, Total -: {len_neg})'

            # Tandai sisa negatif
            if unmatched_neg:
                df.loc[unmatched_neg, 'Status_Rekonsiliasi'] = 'UNMATCHED_DEFICIT'
                df.loc[unmatched_neg, 'Keterangan'] = f'Kelebihan Nilai Negatif (Total +: {len_pos}, Total -: {len_neg})'

        # Filter hanya data yang TIDAK MATCH / UNMATCHED
        unmatched_df = df[df['Status_Rekonsiliasi'] != 'MATCHED'].copy()

        # Rapikan DataFrame sebelum dikirim sebagai JSON
        unmatched_df = unmatched_df.drop(columns=['nilai_clean', 'abs_val'])

        # Mengubah data NaN/NaT menjadi None agar JSON valid
        result_data = unmatched_df.fillna("").to_dict(orient='records')

        return JSONResponse(content={
            "status": "success",
            "total_unmatched_rows": len(result_data),
            "data": result_data
        })

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Terjadi kesalahan saat memproses data: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
