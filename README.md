# bw-remote

Genera la configurazione di **rclone** e **ssh** a partire dagli item di Bitwarden
(Login e Secure Note) che hanno il custom field `remote_type`.

Bitwarden resta l'unica fonte: i file generati non si modificano a mano.

## Requisiti

- Python 3.9+ (va bene quello dei Command Line Tools di macOS), solo libreria standard
- [Bitwarden CLI](https://bitwarden.com/help/cli/) (`bw`) sbloccato
- `rclone` (per offuscare le password)
- OpenSSH (`ssh`, `sftp`, `ssh-copy-id`) per `copy-key`

## Installazione

```sh
git clone git@github.com:thespacersm/bw-remote.git
ln -s "$PWD/bw-remote/bw-remote" /usr/local/bin/bw-remote
```

## Configurare un item in Bitwarden

Su un item esistente (Login o Secure Note) si aggiungono questi custom field:

| Campo | Tipo campo | Esempio |
|---|---|---|
| `remote_type` | testo | `ssh` |
| `remote_name` | testo | `example.com` |
| `remote_host` | testo | `203.0.113.10` |
| `remote_port` | testo | `2222` (solo se diversa dal default) |
| `remote_user` | testo | `example` |
| `remote_pwd` | nascosto | la password |

Risultato:

```ini
# rclone.conf
[example.com]
type = sftp
host = 203.0.113.10
user = example
port = 2222
pass = <offuscata>
```

```
# ~/.ssh/config.d/bw-remote
Host example.com
  HostName 203.0.113.10
  User example
  Port 2222
```

Per rclone si usa `rclone ls example.com:`, per ssh `ssh example.com`.

Su un Login, utente, password e host possono restare nei campi nativi dell'item
(vedi sotto). Su una Secure Note servono sempre i custom field: il testo della nota
non viene letto.

## Campi

Il tool considera solo gli item con `remote_type`; tutti gli altri vengono ignorati.

| Campo | Obbligatorio | Note |
|---|---|---|
| `remote_type` | sì | `ssh` o `sftp` (sinonimi), `ftp`, `ftps` |
| `remote_name` | sì | nome del remote rclone e `Host` ssh |
| `remote_host` | sì* | hostname o IP |
| `remote_user` | sì* | utente |
| `remote_pwd` | no* | password (meglio come campo nascosto) |
| `remote_port` | no | default 22 (ssh/sftp) o 21 (ftp/ftps) |
| `remote_key` | no | chiave privata, es. `~/.ssh/id_ed25519` |
| `remote_alias` | no, ripetibile | alias rclone `nome:path`, es. `example.com.prod:/var/www` |
| `remote_ssh_alias` | no, ripetibile | nome in più sulla riga `Host` ssh |
| `remote_opt` | no, ripetibile | opzione extra: `rclone:chiave=valore` o `ssh:Chiave=valore` |

\* Per i Login, se il campo manca si usano username, password e il primo URI
`ssh://`, `sftp://`, `ftp://`, `ftps://` (o senza schema) dell'item. Se i custom field
sono presenti, vincono sempre loro. Si possono usare anche i campi di tipo "linked".

## Cosa genera

| `remote_type` | rclone | ssh |
|---|---|---|
| `ssh`, `sftp` | remote `type = sftp` | `Host` |
| `ftp` | remote `type = ftp` | - |
| `ftps` | remote `type = ftp` con `explicit_tls = true` | - |

- **rclone**: scrive solo nel blocco `# BEGIN bw-remote` / `# END bw-remote` di
  `rclone.conf` (rispetta `RCLONE_CONFIG`) e lascia intatto il resto del file. Le password
  passano da `rclone obscure`; se l'item non è cambiato, la password offuscata già scritta
  viene riusata, così il diff resta pulito.
- **ssh**: rigenera `~/.ssh/config.d/bw-remote` e aggiunge
  `Include ~/.ssh/config.d/bw-remote` in testa a `~/.ssh/config`, se manca.
- I nomi già definiti a mano fuori dal blocco (sezioni rclone o `Host` in `~/.ssh/config`)
  hanno la precedenza: la voce da Bitwarden viene saltata con un avviso.
- Prima di ogni scrittura salva un backup `<file>.bak-AAAAMMGGhhmmss`.

## Uso

```sh
export BW_SESSION="$(bw unlock --raw)"

bw-remote sync            # bw sync + diff di cosa cambierebbe (password mascherate)
bw-remote sync --apply    # scrive

bw-remote copy-key                    # per ogni host ssh/sftp: la chiave funziona gia?
bw-remote copy-key 'example.*' --apply  # installa la chiave usando remote_pwd
```

`copy-key` per ogni host:

1. verifica se la chiave (`remote_key`, oppure `--key`, default `~/.ssh/id_ed25519`)
   permette già il login, prima con `sftp` e poi con `ssh`;
2. se non lo permette, con `--apply` esegue `ssh-copy-id` con la password presa da
   Bitwarden e passata tramite `SSH_ASKPASS` (niente `sshpass`, la password non finisce
   tra gli argomenti); se il server accetta solo sftp, riprova con `ssh-copy-id -s`;
3. rifà il controllo e stampa l'esito.

Opzioni comuni: `--no-sync` (salta `bw sync`), `--input FILE` (legge un JSON di
`bw list items` invece di chiamare `bw`).

## Test

```sh
python3 -m unittest discover -s tests
```
