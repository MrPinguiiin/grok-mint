# Cloudflare Email Worker

Worker ini menyimpan email dari Cloudflare Email Routing ke D1 dan menyediakan
API inbox generik yang dilindungi Bearer token.

## Prasyarat

- Email Routing untuk `beanbill.me` sudah aktif.
- Catch-all diarahkan ke Worker `temp-mail-grok`.
- D1 `temp-mail-grok-db` sudah tersedia.
- Node.js dan npm tersedia secara lokal.

## Instalasi

```bash
cd cloudflare-email-worker
node --version
```

Perintah npm proyek memakai `npx wrangler@latest`, sehingga Wrangler tidak
disimpan sebagai dependency permanen di repository.

Autentikasi Wrangler tanpa menyimpan token di repository:

```bash
export CLOUDFLARE_API_TOKEN="token-cloudflare-baru"
export CLOUDFLARE_ACCOUNT_ID="account-id-anda"
```

Token Cloudflare minimal membutuhkan izin untuk Workers Scripts dan D1 pada
akun yang bersangkutan. Jangan gunakan token yang pernah dipublikasikan.

## Secret API

Buat token API inbox yang berbeda dari token Cloudflare:

```bash
openssl rand -hex 32 | npx wrangler secret put API_TOKEN
```

Simpan nilai token yang dimasukkan karena token dibutuhkan oleh klien API.

## Migrasi Dan Deploy

Tabel pada database yang sudah dibuat kompatibel dengan migrasi ini. Perintah
berikut aman dijalankan karena memakai `IF NOT EXISTS`:

```bash
npm run db:migrate:remote
npm run deploy
```

Jika binding D1 dibuat lewat dashboard, pastikan tetap bernama `DB`.

## API

Semua request menggunakan header:

```text
Authorization: Bearer <API_TOKEN>
```

Health check:

```bash
curl -H "Authorization: Bearer $MAIL_API_TOKEN" \
  "https://temp-mail-grok.<subdomain>.workers.dev/api/health"
```

Buat alamat acak:

```bash
curl -X POST -H "Authorization: Bearer $MAIL_API_TOKEN" \
  "https://temp-mail-grok.<subdomain>.workers.dev/api/new_address"
```

Daftar pesan untuk satu penerima:

```bash
curl -G -H "Authorization: Bearer $MAIL_API_TOKEN" \
  --data-urlencode "recipient=contoh@beanbill.me" \
  "https://temp-mail-grok.<subdomain>.workers.dev/api/mails"
```

Detail atau hapus pesan:

```bash
curl -H "Authorization: Bearer $MAIL_API_TOKEN" \
  "https://temp-mail-grok.<subdomain>.workers.dev/api/mail/1"

curl -X DELETE -H "Authorization: Bearer $MAIL_API_TOKEN" \
  "https://temp-mail-grok.<subdomain>.workers.dev/api/mail/1"
```
