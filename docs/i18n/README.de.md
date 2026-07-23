# Bookflow Scholar Benutzerhandbuch (Deutsch)

[Download](https://github.com/huanghaitck/bookflow-scholar/releases/tag/v0.8.0-rc.2) · [Problem melden](https://github.com/huanghaitck/bookflow-scholar/issues/new?template=user_problem.yml) · [Roadmap zu 1.0](../ROADMAP_1.0.md) · [Startseite](../../README.md)

## Zweck

Bookflow Scholar ist eine Windows-Desktopanwendung zur Übersetzung und Layout-Rekonstruktion von Aufsätzen, Büchern und Monografien. Seitenübergreifende logische Einheiten werden zuerst vollständig wiederhergestellt und gemeinsam übersetzt; anschließend setzt die Anwendung `【Originalseite】` an der tatsächlichen Seitengrenze ein. Deterministische Verarbeitung übernimmt reproduzierbare Schritte, multimodale Modelle unterstützen die Analyse komplexer Layouts und visueller Objekte.

Wesentliche Verbesserungen:

- Fließtext, Kopf- und Fußzeilen, Fußnoten und Endnoten werden getrennt segmentiert, übersetzt und positioniert;
- Bilder, Karten, Abbildungen, Beschriftungen und Tabellen werden aus ihrem Kontext rekonstruiert; irrelevante Copyright-Grafiken können entfallen;
- Glossaränderungen gelten exakt für Quelle, Übersetzungseinheit und occurrence/span;
- schwierige Seiten werden objektbezogen und nicht destruktiv zurückgeführt;
- dynamische Dateinamen für Quell-, Zielsprach- und zweisprachige Ausgabe;
- Pause, Fortsetzen, Fortsetzen nach Neustart, Abbruch und Wiederholung nach Fehlern;
- Vorschau des fertigen PDF mit Zurück, Weiter und direkter Seiteneingabe;
- Chinesisch (vereinfacht), Englisch, Französisch, Deutsch, Japanisch und Spanisch; alle 30 Übersetzungsrichtungen wurden abgedeckt.

## Erste Schritte

1. Installieren Sie `Bookflow-Scholar-0.8.0-rc.2-setup.exe`, oder entpacken Sie das portable ZIP und starten Sie `Bookflow Scholar.exe`.
2. Wählen Sie **Create project**. Erst das Projekt stellt Arbeitsbereich und Kontext für ein PDF bereit.
3. Öffnen Sie das Projekt, konfigurieren Sie Text- und Vision-Provider, Modelle und API-Schlüssel und speichern Sie. Schlüssel werden in der Windows-Anmeldeinformationsverwaltung gespeichert.
4. Wählen Sie **Import PDF** und anschließend Quell- und Zielsprache. Wählen Sie bei mehreren Quellen ausdrücklich die aktive Quelle.
5. Wählen Sie **Start**. Der Auftrag kann pausiert, fortgesetzt, abgebrochen oder nach einem Neustart wiederaufgenommen werden.
6. Prüfen Sie das fertige PDF in Overview; navigieren Sie mit Zurück, Weiter oder `aktuelle Seite/gesamt`.
7. Glossar- und Problemseitenpakete entstehen nur bei vorhandenen Kandidaten. Folgen Sie der enthaltenen offiziellen Eingabeaufforderung und importieren Sie das ausgefüllte Paket.
8. Wählen Sie **Open output folder**, um alle drei Ausgaben zu öffnen.

## Installation und Sicherheit

Dieser Release Candidate ist nicht signiert; Windows kann SmartScreen anzeigen. Prüfen Sie den SHA-256 auf der Release-Seite oder verwenden Sie das portable ZIP. [LibreOffice kann von der offiziellen Website geladen werden](https://www.libreoffice.org/download/); es ist optional, aber empfohlen.

Veröffentlichen Sie keine vertraulichen Dokumente, API-Schlüssel, Autorisierungsheader, privaten Pfade oder personenbezogenen Daten. Nutzen Sie das kostenlose [GitHub-Problemformular](https://github.com/huanghaitck/bookflow-scholar/issues/new?template=user_problem.yml).
