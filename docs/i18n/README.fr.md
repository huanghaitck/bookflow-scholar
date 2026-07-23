# Guide d’utilisation de Bookflow Scholar (français)

[Télécharger](https://github.com/huanghaitck/bookflow-scholar/releases/tag/v0.8.0-rc.2) · [Signaler un problème](https://github.com/huanghaitck/bookflow-scholar/issues/new?template=user_problem.yml) · [Feuille de route 1.0](../ROADMAP_1.0.md) · [Accueil](../../README.md)

## Fonction

Bookflow Scholar est une application Windows de traduction et de reconstruction de mise en page pour les articles, livres et monographies. Elle rétablit les unités logiques coupées par un changement de page, les traduit dans leur ensemble, puis réinsère les repères `【page d’origine】` à la limite réelle. Le traitement déterministe assure les opérations reproductibles, tandis qu’un modèle multimodal aide à comprendre les mises en page et objets visuels complexes.

Améliorations principales :

- corps du texte, en-têtes, pieds de page, notes de bas de page et notes finales sont séparés, traduits et replacés indépendamment ;
- images, cartes, figures, légendes et tableaux sont reconstruits selon leur contexte ; les illustrations de copyright sans rapport peuvent être écartées ;
- corrections terminologiques ciblées par source, unité de traduction et occurrence/span ;
- retour non destructif des pages difficiles au niveau de l’objet ;
- éditions source, cible et bilingue avec noms dynamiques ;
- pause, reprise, reprise après redémarrage, annulation et nouvel essai ;
- aperçu du PDF final avec page précédente, suivante et accès direct ;
- chinois simplifié, anglais, français, allemand, japonais et espagnol, soit 30 directions testées.

## Première utilisation

1. Installez `Bookflow-Scholar-0.8.0-rc.2-setup.exe`, ou décompressez le ZIP portable et lancez `Bookflow Scholar.exe`.
2. Cliquez sur **Create project**. Le projet fournit l’espace de travail et le contexte nécessaires au PDF.
3. Ouvrez le projet, configurez les fournisseurs texte et vision, les modèles et les clés API, puis enregistrez. Les clés sont conservées dans le Gestionnaire d’informations d’identification Windows.
4. Cliquez sur **Import PDF**, choisissez les langues source et cible. Dans un projet multi-source, choisissez explicitement la source active.
5. Cliquez sur **Start**. Vous pouvez mettre en pause, reprendre, annuler ou reprendre après redémarrage.
6. Vérifiez le PDF final dans Overview et utilisez Précédent, Suivant ou `page actuelle/total`.
7. Les paquets de glossaire et de pages difficiles ne sont produits que s’il existe des candidats. Suivez l’invite officielle incluse, puis réimportez le paquet.
8. Cliquez sur **Open output folder** pour obtenir les trois éditions.

## Installation et sécurité

Cette version candidate n’est pas signée ; Windows peut afficher SmartScreen. Vérifiez le SHA-256 publié dans la Release ou utilisez le ZIP portable. [Téléchargez LibreOffice depuis le site officiel](https://www.libreoffice.org/download/) ; il est facultatif mais recommandé.

Ne publiez jamais de document confidentiel, clé API, en-tête d’autorisation, chemin privé ou donnée personnelle. Utilisez le [formulaire GitHub gratuit](https://github.com/huanghaitck/bookflow-scholar/issues/new?template=user_problem.yml).
