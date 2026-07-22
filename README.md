<div align="center">

# Grok Mint

**Otomasi provisioning akun Grok dan integrasi native ke 9Router.**

<p>
  <img src="https://img.shields.io/badge/Python-3.14-3776AB.svg?style=flat-square&logo=python" alt="Python 3.14">
  <img src="https://img.shields.io/badge/OS-Linux-success.svg?style=flat-square&logo=linux" alt="Linux">
  <img src="https://img.shields.io/badge/Interface-GUI%20%2B%20CLI-blue.svg?style=flat-square" alt="GUI + CLI">
  <img src="https://img.shields.io/badge/9Router-Auto%20Import-purple.svg?style=flat-square" alt="9Router Auto Import">
  <img src="https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square" alt="MIT">
</p>

**Maintained by [MrPinguiiin](https://github.com/MrPinguiiin)**

</div>

## Tentang

Grok Mint adalah toolkit Python untuk menjalankan alur registrasi berbasis browser, menerima OTP melalui mailbox sementara Wapol, membuat kredensial Grok CLI OAuth, dan mengimpor koneksi yang berhasil ke instalasi 9Router lokal.

Fitur utama:

- GUI Tkinter dan CLI interaktif.
- Browser automation dengan DrissionPage dan Google Chrome.
- Provider mailbox `wapol.site`, DuckMail, MailTM, dan Cloudflare Temp Mail.
- Ekstraksi serta pengisian OTP otomatis.
- Ekspor kredensial CPA xAI/Grok CLI.
- Auto-import idempotent ke provider native `grok-cli` di 9Router.
- Backup database 9Router sebelum setiap import.
- Dukungan worker paralel.

> [!WARNING]
> Gunakan hanya untuk penelitian, pengujian integrasi, dan penggunaan yang sah. Pengguna bertanggung jawab mematuhi ketentuan layanan penyedia, aturan jaringan, serta hukum yang berlaku.

## Persyaratan

- Linux x86_64.
- Python 3.14.
- Google Chrome atau Chromium.
- Tk 8.6 untuk mode GUI.
- 9Router lokal untuk fitur auto-import.

Arch/CachyOS:

```bash
sudo pacman -S python tk google-chrome
```

## Instalasi

```bash
git clone https://github.com/MrPinguiiin/grok-mint.git
cd grok-mint
chmod +x run.sh
./run.sh
```

`run.sh` otomatis membuat virtual environment, memasang dependencies, dan membuat `config.json` dari template jika belum tersedia.

## Menjalankan

CLI:

```bash
./run.sh
```

Ketik `start` untuk memulai dan `exit` untuk keluar.

GUI:

```bash
./run.sh gui
```

## Konfigurasi Email

Provider bawaan adalah Wapol:

```json
{
  "email_provider": "wapol",
  "wapol_domain": "wapol.site"
}
```

Provider lain yang tersedia: `duckmail`, `mailtm`, dan `cloudflare`. Domain email sementara dapat ditolak oleh x.ai sewaktu-waktu; domain Wapol telah diuji pada implementasi ini.

## Integrasi 9Router

Grok Mint mendeteksi dan menggunakan database instalasi 9Router lokal. Contoh konfigurasi:

```json
{
  "nine_router_auto_import": true,
  "nine_router_base_url": "http://127.0.0.1:20128",
  "nine_router_db_path": "~/.9router/db/data.sqlite"
}
```

Setelah registrasi berhasil, Grok Mint akan:

1. Menyimpan hasil akun dan SSO secara lokal.
2. Membuat kredensial CPA di `cpa_auths/`.
3. Membuat backup database 9Router.
4. Membuat atau memperbarui koneksi native `grok-cli` berdasarkan email.
5. Menyimpan access token, refresh token, ID token, expiry, dan identitas provider.

Backup database disimpan di:

```text
~/.9router/db/backups/grok-mint/
```

Koneksi dapat dilihat di:

```text
http://localhost:20128/dashboard/providers
```

## Output Lokal

File berikut tidak disertakan dalam Git:

- `config.json`
- `accounts_*.txt`
- `tokens.txt`
- `mail_credentials.txt`
- `cpa_auths/`
- `screenshots/`
- `venv/`

## Lisensi

MIT. Lihat [`LICENSE`](LICENSE). Copyright proyek asal tetap dipertahankan sesuai ketentuan lisensi; port Linux dan modifikasi berikutnya dikelola oleh MrPinguiiin.
