# Miller Columns

Un file manager GTK 3 orientato alla tastiera e basato sulla visualizzazione a
colonne Miller. Funziona come applicazione autonoma e può, facoltativamente,
diventare il gestore predefinito delle cartelle per l'utente corrente.

Il progetto nasce come fork di
[linux-nemo-miller-columns](https://github.com/davemin/linux-nemo-miller-columns).

*[Read in English](README.md)*

## Funzionalità

- Colonne Miller stabili e ridimensionabili con anteprima della cartella figlia.
- Navigazione nativa Up/Down e navigazione esplicita tra colonne con Left/Right.
- Elemento attivo separato dagli elementi marcati per operazioni multiple.
- Space modifica un marcatore, Shift estende un intervallo e Ctrl+A marca tutta
  la colonna.
- Copia, taglia, incolla, rinomina, nuova cartella e spostamento nel Cestino.
- Clipboard file compatibile con GNOME/Nemo.
- Drag and drop multiplo in entrata e in uscita.
- Anteprime di testo e immagini con limiti di lettura.
- Pannello metadati, ricerca ricorsiva, breadcrumb e azioni terminale/Nemo.
- Nessuna eliminazione permanente e nessuna sovrascrittura silenziosa.

## Requisiti

- Python 3.10 o successivo.
- GTK 3 e PyGObject (`python3-gi` nelle distribuzioni della famiglia Debian).
- Dati di introspezione GTK, GdkPixbuf e Gio.
- `xdg-utils` solo per `install.sh --default`.
- Nemo e `nemo-python` sono opzionali e servono soltanto per la vecchia
  estensione del menu contestuale.

Le dipendenze non vengono installate automaticamente. Se manca qualcosa, usa
il package manager della tua distribuzione Linux.

## Avvio dal sorgente

```bash
python3 nemo_miller_columns.py
python3 nemo_miller_columns.py /percorso/della/cartella
```

Senza percorso viene aperta la home dell'utente corrente.

## Installazione per l'utente

Installa l'applicazione autonoma e il launcher senza cambiare il file manager
predefinito:

```bash
./install.sh
```

Per impostare esplicitamente Miller Columns come gestore predefinito delle
cartelle per l'utente corrente:

```bash
./install.sh --default
```

L'installer:

- non usa mai `sudo`;
- non installa nulla in `/usr`;
- non installa né riavvia l'estensione Nemo;
- salva il gestore precedente prima di modificarlo;
- installa soltanto in `${XDG_DATA_HOME:-~/.local/share}`.

Disinstallazione:

```bash
./uninstall.sh
```

Se `--default` ha registrato un gestore precedente, l'uninstaller lo ripristina.
Nemo di sistema non viene mai rimosso o modificato.

## Tastiera e mouse

| Input | Azione |
|---|---|
| `Up` / `Down` | Sposta l'elemento attivo nella colonna corrente |
| `Left` | Passa alla colonna Miller precedente |
| `Right` / `Enter` | Entra nella cartella attiva o apre il file attivo |
| `Backspace` | Va alla cartella padre nel filesystem |
| `Space` | Aggiunge/rimuove il marcatore dell'elemento attivo |
| `Shift+Up/Down` | Estende l'intervallo marcato |
| `Ctrl+A` | Marca tutti gli elementi della colonna corrente |
| `Ctrl+C` / `Ctrl+X` / `Ctrl+V` | Copia, taglia e incolla |
| `F2` | Rinomina un singolo elemento attivo/marcato |
| `Delete` | Sposta gli elementi attivi/marcati nel Cestino |
| `Ctrl+Shift+N` | Crea una cartella nella destinazione attiva |
| `Ctrl+F` | Porta il focus sulla ricerca |
| `Esc` | Esce dalla ricerca, cancella i marcatori, poi chiude |
| Click | Rende attivo un elemento; un file diventa l'unico marcato |
| `Ctrl+Click` | Aggiunge/rimuove un marcatore |
| `Shift+Click` | Marca un intervallo |
| Doppio click | Entra in una cartella o apre un file |

L'elemento attivo controlla anteprima e navigazione. Gli elementi marcati sono
soltanto obiettivi delle operazioni: marcare più cartelle non crea colonne
figlie in competizione.

## Operazioni sui file

- Viene usato prima l'insieme marcato; in sua assenza, l'elemento attivo.
- Incolla/nuova cartella usa la cartella attiva oppure la directory della
  colonna quando l'elemento attivo è un file o non esiste.
- Le directory vengono copiate ricorsivamente e i link simbolici sono
  preservati quando possibile.
- Una destinazione esistente viene rifiutata, mai sovrascritta.
- È vietato copiare o spostare una cartella dentro sé stessa o un discendente.
- `Delete` usa il Cestino Gio senza fallback a eliminazione permanente.
- Dopo un taglio parzialmente fallito, i percorsi falliti restano in clipboard.

## Limiti noti

- Undo/redo non è ancora disponibile.
- Non esiste un monitor generale del filesystem: le operazioni interne
  aggiornano le colonne visibili, mentre i cambiamenti esterni possono
  richiedere una nuova navigazione.
- Copie/spostamenti ricorsivi e alcune anteprime possono bloccare il thread GTK.
- Clipboard e drag/drop accettano soltanto percorsi locali `file://`.
- Cancellazione e risultati obsoleti della ricerca richiedono ulteriore lavoro.
- Un desktop che usa un'API D-Bus privata può ignorare l'associazione standard
  `inode/directory`.

## Sviluppo

Test senza display:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

Controllo sintattico senza bytecode nel repository:

```bash
PYTHONPYCACHEPREFIX=/tmp/miller-columns-pycache \
python3 -m py_compile nemo_miller_columns.py \
  nemo-miller-columns-extension.py tests/*.py
```

I test del filesystem usano directory temporanee dedicate in `/tmp`. Non usare
dati personali per provare operazioni mutanti.

## Struttura del repository

```text
assets/                            Immagine fallback delle anteprime
nemo_miller_columns.py             Applicazione GTK autonoma
nemo-miller-columns-extension.py   Sorgente opzionale della vecchia estensione
tests/                              Test della libreria standard
install.sh                          Installer autonomo e sicuro per utente
uninstall.sh                        Uninstaller per utente
```

L'immagine fallback è stata generata appositamente per questo progetto ed è
distribuita con la licenza MIT del repository.

## Licenza

[MIT](LICENSE). L'avviso di copyright originale è conservato.
