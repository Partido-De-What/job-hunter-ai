# Job Hunter AI — come iniziare

**Job Hunter AI** è un assistente per la ricerca di lavoro che gira **sul tuo computer**. Ogni giorno:

- raccoglie nuove offerte di lavoro;
- scarta quelle che non c'entrano;
- valuta le altre rispetto al tuo profilo con Claude;
- prepara CV e lettere di presentazione su misura per le offerte migliori;
- ti manda un breve riepilogo via email.

**Non invia mai niente ai datori di lavoro**: decidi tu, e ti candidi tu.

## Cosa ti serve

- Un PC **Windows** con **Microsoft Word**, per i PDF.
- **Python** 3.12 o più recente (https://www.python.org) e **Git** (https://git-scm.com).
- Un abbonamento **Claude** (Pro o Max) e **Claude Code** (https://claude.com/claude-code).

## Primi passi (circa 30–60 minuti, una volta sola)

1. **Estrai lo ZIP** in una cartella, per esempio sul Desktop.
2. Dentro quella cartella crea una cartella **`cv`** e mettici il tuo **CV** (PDF o Word) e, se vuoi, una **foto**.
3. Apri **Claude Code**, accedi con il tuo account Claude e apri la cartella del programma.
4. Scrivi: **"Configurami"** (oppure "Set me up").
5. Claude ti guida passo per passo:
   - installa tutto;
   - crea il tuo profilo dal CV e ti fa domande su ciò che manca. **Rispondi con sincerità**: il programma non inventa mai esperienze o livelli di lingua;
   - imposta la tua zona, il tuo settore, le lingue e lo stile del CV;
   - cerca e verifica i siti di lavoro adatti alla tua zona;
   - fa con te una prima prova su 5 offerte.

## Uso quotidiano

- Ogni mattina (se attivi l'esecuzione automatica) ricevi un'email con le offerte migliori.
- Apri il pannello con `vjh dashboard` per leggere le valutazioni, i CV e le lettere.
- Se un'offerta ti interessa: controlla i documenti, approvali, **candidati tu** e poi registra la candidatura (`vjh track set <numero> applied`).
- Se un'offerta non fa per te, dillo (`vjh track dismiss <numero> "motivo"`): il programma impara cosa cerchi.

Tutte le istruzioni dettagliate (in inglese) sono nel file **README.md**. Per qualsiasi problema puoi chiedere direttamente a Claude Code, in italiano.

## Da sapere

- **Privacy:** CV, profilo, documenti e dati restano sul tuo computer. A Claude vengono inviati solo i testi delle offerte e i fatti del tuo profilo che servono alla valutazione. Indirizzo, telefono, email e data di nascita non vengono mai inviati.
- **LinkedIn, Indeed e Glassdoor** (tramite JobSpy) sono **attivi**. Questi siti vietano la raccolta automatica nelle loro condizioni d'uso e potrebbero bloccare temporaneamente la tua connessione. JobSpy non accede mai al tuo account. Se preferisci non usarli, scrivi a Claude "disattiva JobSpy", oppure metti `allow_jobspy: false` in `config\settings.yaml`.
- **Costi:** con l'abbonamento Claude non ci sono costi extra. Il programma valuta al massimo 40 offerte al giorno, per restare nei limiti del tuo piano.
