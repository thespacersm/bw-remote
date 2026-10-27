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

## Configurazione

Facoltativa: `~/.config/bw-remote/config` (rispetta `XDG_CONFIG_HOME`, oppure
`--config FILE`), in formato INI. Esempio completo in [`config.example`](config.example):

```ini
[defaults]
key = ~/.ssh/id_rsa
rclone_config = ~/.config/rclone/rclone.conf
ssh_config = ~/.ssh/config
ssh_include = ~/.ssh/config.d/bw-remote
```

Precedenza: opzione da riga di comando, poi `RCLONE_CONFIG` (solo per `rclone_config`),
poi il file, poi i default qui sopra. Chiavi o sezioni sconosciute danno un avviso.

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
| `remote_key` | no | chiave privata; se manca (e non c'è `remote_pwd`) rclone usa `--key`, default `~/.ssh/id_rsa` |
| `remote_alias` | no, ripetibile | alias rclone `nome:path`, es. `example.com.prod:/var/www` |
| `remote_ssh_alias` | no, ripetibile | nome in più sulla riga `Host` ssh |
| `remote_opt` | no, ripetibile | opzione extra: `rclone:chiave=valore` o `ssh:Chiave=valore` |
| `remote_via` | no | `remote_name` della macchina da cui si raggiunge il sito (vedi sotto) |
| `remote_path` | no | cartella del sito sulla macchina, solo con `remote_via` |
| `remote_via_sudo` | no | utente con cui accedere ai file del sito dalla macchina (`sudo -u`), solo con `remote_via` |

\* Per i Login, se il campo manca si usano username, password e il primo URI
`ssh://`, `sftp://`, `ftp://`, `ftps://` (o senza schema) dell'item. Se i custom field
sono presenti, vincono sempre loro. Si possono usare anche i campi di tipo "linked".

## Cosa genera

| `remote_type` | rclone | ssh |
|---|---|---|
| `ssh`, `sftp` | remote `type = sftp` | `Host` |
| `ftp` | remote `type = ftp` | - |
| `ftps` | remote `type = ftp` con `explicit_tls = true` | - |

- **rclone**: in `rclone.conf` (rispetta `RCLONE_CONFIG`) rigenera solo le sezioni con
  `description = bw-remote <id item> <revisione> <nome item>`, in fondo al file, e lascia
  intatte quelle scritte a mano. Si usa la description e non dei commenti perché rclone
  riscrive il file (perdendo i commenti) quando salva i valori che rileva al primo uso;
  quei valori (`shell_type`, `md5sum_command`, `sha1sum_command`) vengono conservati.
  Le password passano da `rclone obscure`; se l'item non è cambiato, la password offuscata
  già scritta viene riusata, così il diff resta pulito. Per vedere i remote generati:
  `rclone listremotes --long --description bw-remote`.
- **ssh**: rigenera `~/.ssh/config.d/bw-remote` e aggiunge
  `Include ~/.ssh/config.d/bw-remote` in testa a `~/.ssh/config`, se manca.
- Se più item hanno lo stesso `remote_name`, in ogni file il nome senza suffisso va a:
  1. l'item che non ha `OLD` nel nome,
  2. poi al tipo più adatto al file (rclone: `sftp`, `ftps`, `ftp`, `ssh`, via macchina;
     ssh: via macchina, `ssh`, `sftp`),
  3. poi al più recente.

  Gli altri prendono il suffisso `.old` (se l'item ha `OLD` nel nome) o `.<remote_type>`,
  più `-2`, `-3`… se serve. Per esempio, un sito con un item `ssh` (utente con shell) e uno
  `sftp` (utente chroot) diventa:

  | | rclone | ssh |
  |---|---|---|
  | item `sftp` | `example.com` | `example.com.sftp` |
  | item `ssh` | `example.com.ssh` | `example.com` |
  | item `OLD …` | `example.com.old` | `example.com.old` |

  Con un solo item `ssh` e `sftp` restano sinonimi. Gli alias (`remote_alias`,
  `remote_ssh_alias`) invece devono essere unici: un alias duplicato viene saltato.
- I nomi già definiti a mano (sezioni rclone senza la description di bw-remote o `Host` in `~/.ssh/config`)
  hanno la precedenza: la voce da Bitwarden viene saltata con un avviso.
- Prima di ogni scrittura salva un backup `<file>.bak-AAAAMMGGhhmmss`.

## Siti raggiungibili da una macchina (`remote_via`)

Quando un sito ha un suo accesso SFTP (spesso chroot) e in più si raggiunge da una
macchina su cui si ha un accesso ssh globale:

1. l'item della macchina ha i suoi campi (`remote_type ssh`, `remote_name example-host`, …);
2. l'item del sito ha, oltre ai suoi, `remote_via example-host`, `remote_path /srv/siti/example`
   e, se l'utente della macchina non può leggere i file del sito, `remote_via_sudo example`.

Risultato:

| | rclone | ssh |
|---|---|---|
| `example.com` | SFTP diretto (utente del sito) | ssh con utente e chiave della macchina |
| `example.com.ssh` | alias `example-host:/srv/siti/example` | - |
| `example.com.sftp` | - | SFTP diretto (utente del sito) |

In rclone resta principale l'accesso diretto, così i file caricati restano dell'utente
del sito; in ssh è principale la shell dalla macchina. Un item con `remote_via` ma
senza host e utente genera solo l'accesso dalla macchina. `copy-key` salta gli accessi
via macchina: la chiave va copiata sulla macchina una volta sola.

Con `remote_via_sudo` l'alias rclone passa dal client `ssh` (connessione presa da
`~/.ssh/config`, `Host` della macchina) e lancia `sftp-server` e gli hash con
`sudo -n -u <utente>`: i file letti e scritti sono quelli dell'utente del sito.
Serve sudo senza password per l'utente della macchina e `sftp-server` in
`/usr/lib/openssh/sftp-server` (Debian/Ubuntu). Il client `ssh` apre una connessione
per ogni hash: va bene per un sito, è lento su `rclone check` di migliaia di file.

Perché rclone non usa sempre il client `ssh`: la sua libreria sftp è più veloce, funziona
con la password e non dipende da `~/.ssh/config`; il client serve solo dove la libreria
non arriva (qui il cambio utente con sudo).

## Uso

```sh
export BW_SESSION="$(bw unlock --raw)"

bw-remote sync            # bw sync + diff di cosa cambierebbe (password mascherate)
bw-remote sync --apply    # scrive

bw-remote copy-key                    # per ogni host ssh/sftp: la chiave funziona gia?
bw-remote copy-key 'example.*' --apply  # installa la chiave usando remote_pwd
```

`copy-key` per ogni host:

1. verifica se la chiave (`remote_key`, oppure `--key`, default `~/.ssh/id_rsa`)
   permette già il login, prima con `sftp` e poi con `ssh`;
2. se non lo permette, con `--apply` esegue `ssh-copy-id` con la password presa da
   Bitwarden e passata tramite `SSH_ASKPASS` (niente `sshpass`, la password non finisce
   tra gli argomenti); se il server accetta solo sftp, riprova con `ssh-copy-id -s`;
3. rifà il controllo e stampa l'esito.

Opzioni comuni: `--no-sync` (salta `bw sync`), `--input FILE` (legge un JSON di
`bw list items` invece di chiamare `bw`), `--config FILE`, `--key FILE` (chiave per gli item senza
`remote_key`, default `~/.ssh/id_rsa`).

## Test

```sh
python3 -m unittest discover -s tests
```
