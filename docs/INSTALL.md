# Installing Mizani in a hospital

For the hospital's IT team. Mizani runs on one server in the hospital; nurses and doctors open it in a browser on tablets and desk PCs, over HTTPS, with their own accounts. Nothing is installed on the tablets, and after the first start the server needs no internet.

Mizani is a prototype tested on synthetic patients only. Use synthetic data until the hospital's ethics and data-protection approvals are in place.

## 1. The server

| | Without a GPU | With an NVIDIA GPU |
|---|---|---|
| Processor | 4 cores or more | 4 cores or more |
| Memory | 16 GB (Mizani peaked at 5.5 GB in our tests) | 16 GB |
| GPU | none | 4 GB of video memory or more |
| Disk | 30 GB free, SSD recommended | 30 GB free, SSD recommended |
| Time per patient (measured) | 1 to 2 minutes (MacBook Air M4, model inside Docker, no GPU) | 14 to 17 s (GTX 1650 with 4 GB, the model warm) |

Time per patient is almost all the language model. A larger model, or the model on a separate GPU server, is a settings change ([REMOTE_MODEL.md](REMOTE_MODEL.md)).

Software: Linux (for example Ubuntu 24.04 LTS) with Docker Engine and its Compose plugin, or Windows or macOS with Docker Desktop. For an NVIDIA GPU on Linux, the NVIDIA driver and the NVIDIA Container Toolkit. Git.

Network: a fixed address on the hospital network and, ideally, a name in the hospital's DNS (for example `triage.hospital.local`). Tablets and PCs reach one port, HTTPS. Everything else listens on the server itself only.

Internet: for the first start (about 7 GB of images and the model) and for updates. Not while Mizani runs.

## 2. First start

```
git clone https://github.com/Mo2mexen/Mizani.git
cd Mizani
```

Create a file named `.env` in that folder:

```
MIZANI_HOSTNAME=triage.hospital.local
MIZANI_LISTEN=0.0.0.0
HTTPS_HOST_PORT=443
```

- `MIZANI_HOSTNAME`: the name, or the IP address, the tablets will open.
- `MIZANI_LISTEN=0.0.0.0`: opens the HTTPS port to the network. Without it, HTTPS answers on the server itself only.
- `HTTPS_HOST_PORT=443`: the usual HTTPS port. The default is 8443, because 443 is often taken on a laptop.
- With an NVIDIA GPU, add two lines so the model uses it:

  ```
  COMPOSE_PATH_SEPARATOR=:
  COMPOSE_FILE=docker-compose.yml:docker/docker-compose.gpu.yml
  ```

Then start it, in the background:

```
docker compose up -d
docker compose ps
```

The first start downloads the images and the model; later starts take seconds. When `docker compose ps` shows `app` as healthy, open `https://<MIZANI_HOSTNAME>` from the server. Read section 4 before you open it from a tablet.

## 3. The first admin and the accounts

There is no signup page. On the first start, the app writes a one-time password for the account `admin` in its log:

```
docker compose logs app
```

```
+---------------------------------------------------------+
| First sign-in to Mizani                                 |
|   user name:          admin                             |
|   one-time password:  kq7m-x3pd-9fwt                    |
| Open the page, sign in, and choose your own password.   |
| Until then, every start prints a new one-time password. |
+---------------------------------------------------------+
```

Sign in as `admin` with it and choose your own password. Then, under **Users**, add each person who will use Mizani:

- **Nurse:** the triage desk. Intake, approve or override, re-check, handover, called in.
- **Doctor:** the waiting room in priority order, the handovers nurses release, and "Seeing this patient". A doctor never changes a colour.
- **Admin:** accounts and settings. No clinical actions.
- **Chief flag:** given to one doctor (the chief doctor or clinical lead) under Manage.

Each person gets a one-time password from you, given on paper or in person, and chooses their own at their first sign-in. Give every person their own account: the name on each saved decision is the account's. When someone leaves, deactivate the account (Manage, Deactivate); accounts are never deleted, so their name stays on the records they made.

Prefer a command, or locked out of every admin account? On the server:

```
docker compose run --rm --no-deps app mizani-admin create-admin <user name> "<full name>"
docker compose run --rm --no-deps app mizani-admin reset-password <user name>
```

Each prints a one-time password.

## 4. The HTTPS certificate

Tablets only trust a certificate signed by an authority they know. Choose one of two ways.

### A. The hospital's own certificate (preferred)

1. Get a certificate for `MIZANI_HOSTNAME` from the hospital's certificate authority, as PEM files: the certificate with its chain, and the private key.
2. Put them in the `certs` folder next to `docker-compose.yml`, named `mizani.crt` and `mizani.key`. The folder is never committed to git or copied into an image.
3. Add one line to `.env`: `MIZANI_TLS=/certs/mizani.crt /certs/mizani.key`
4. Restart Caddy: `docker compose up -d caddy`

Devices that already trust the hospital's authority need nothing more.

### B. Mizani's own certificate authority

If you leave `MIZANI_TLS` unset, Caddy makes its own authority ("Mizani local CA") and signs a certificate for `MIZANI_HOSTNAME` with it, renewing it by itself. Each tablet and PC must trust that authority once.

Copy the authority's certificate from the server:

```
docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt ./mizani-root.crt
```

Then install `mizani-root.crt` on each device. Menus differ a little between versions and makers.

| Device | Steps |
|---|---|
| Android | Copy the file to the tablet. Settings, Security (or Security and privacy), Encryption and credentials, Install a certificate, CA certificate; choose the file. |
| iPad, iPhone | Send the file (AirDrop or email) and open it. Settings, Profile Downloaded, Install. Then Settings, General, About, Certificate Trust Settings: turn on "Mizani local CA". |
| Windows | Double-click the file, Install Certificate, Local Machine, "Place all certificates in the following store", Trusted Root Certification Authorities. |
| macOS | Open the file in Keychain Access, add it to the System keychain, open it, Trust, "Always Trust". |
| Firefox (any system) | Settings, Privacy and Security, Certificates, View Certificates, Authorities, Import. |

The authority lives in the Docker volume `mizani_caddy-data`. Keep that volume: if it is deleted, Caddy makes a new authority and every device must trust the new one.

To check a device before the nurses use it, open `https://<MIZANI_HOSTNAME>` (add `:<port>` if it is not 443). The page must open without a certificate warning, and the browser must show a lock.

## 5. Tablets and desk PCs

- Open `https://<MIZANI_HOSTNAME>`; on a tablet, "Add to home screen" makes it an icon.
- Each person signs in with their own account. A page left alone for 15 minutes signs out by itself; "Switch user" in the header hands a shared tablet to the next person in one click. A session ends after 12 hours in any case.
- Five wrong passwords lock an account for 15 minutes; an admin's password reset lifts the lock.
- The browser keeps no patient data: every answer from the server is marked not to be cached.

## 6. Backups

One command makes a dated backup in `backups/` (the triage database with the accounts and the audit log, the agent's saved runs, the FHIR store), checks it, and prints the audit log's latest hash:

```
docker compose run --rm backup
```

Run it every night, and copy the `backups` folder to another machine; write the printed audit head somewhere other than this server. Examples, with the Mizani folder at `/opt/mizani` or `C:\Mizani`:

- Linux, cron (`crontab -e`): `0 2 * * * cd /opt/mizani && docker compose run --rm backup >> backups/backup.log 2>&1`
- Windows, Task Scheduler: a daily task running `docker` with the arguments `compose run --rm backup`, starting in `C:\Mizani`.

To restore: `docker compose down`, then `docker compose run --rm backup restore <folder>`, then `docker compose up -d`. A backup that changed since it was made is refused. Details: [systems/backup.md](systems/backup.md).

## 7. Updates

Each version is listed in [CHANGELOG.md](../CHANGELOG.md). Back up first, then update and rebuild:

```
docker compose run --rm backup
git pull
docker compose up -d --build
```

Then check the version: the page shows it at the bottom, and so does the health address, which needs no sign-in:

```
curl -k https://<MIZANI_HOSTNAME>/api/health      # {"status":"ok","version":"1.0.0"}
```

The waiting room, the accounts and the audit log are kept in Docker volumes and survive an update; new tables and columns are added on the first start. If something goes wrong, go back: `git checkout <the commit or tag of the version before>`, `docker compose down`, `docker compose run --rm backup restore <the backup you just made>`, `docker compose up -d --build`.

## 8. When something goes wrong

| What you see | Why, and what to do |
|---|---|
| A tablet cannot reach the page | `MIZANI_LISTEN=0.0.0.0` missing from `.env`, the server's firewall closes the HTTPS port, or the name does not resolve on the tablet's network. Try the server's IP address; check `docker compose ps`. |
| A certificate warning | The device does not trust the authority yet (section 4), or the name typed is not the `MIZANI_HOSTNAME` the certificate is for. |
| "Invalid host header" | The page was opened with a name Mizani does not answer to. Use `MIZANI_HOSTNAME`, or list the extra name in `MIZANI_ALLOWED_HOSTS` (see `.env.example`), then `docker compose up -d`. |
| "Wrong user name or password" | Check the user name; an admin can reset the password under Users. |
| "This account is locked for N more minutes" | Five wrong passwords. Wait, or an admin resets the password. |
| Every admin is locked out or forgot the password | `docker compose run --rm --no-deps app mizani-admin reset-password <user name>` on the server. |
| The first-sign-in box is gone from the log | Restart the app (`docker compose restart app`): until an admin chooses a password, each start prints a new one. |
| A patient takes minutes | The model runs on the processor. Use the GPU file (section 2), or a model server with a GPU ([REMOTE_MODEL.md](REMOTE_MODEL.md)). The first patient after a start also waits while the model loads. |
| A service shows as unhealthy | `docker compose ps` names it; `docker compose logs <service>` says why. `docker compose up -d` starts what stopped. |
| A port is already used | Set `HTTPS_HOST_PORT` (or `API_HOST_PORT` and the others in `.env.example`) to a free port. |

On the server itself the page also answers at `http://127.0.0.1:8090`, without HTTPS; that port is never reachable from the network.
