# Bookflow Scholar ユーザーガイド（日本語）

[ダウンロード](https://github.com/huanghaitck/bookflow-scholar/releases/tag/v0.8.0-rc.2) · [問題を報告](https://github.com/huanghaitck/bookflow-scholar/issues/new?template=user_problem.yml) · [1.0 ロードマップ](../ROADMAP_1.0.md) · [ホーム](../../README.md)

## このツールについて

Bookflow Scholar は、論文・書籍・専門書を翻訳し、レイアウトを再構築する Windows デスクトップアプリです。改ページをまたぐ論理単位を先に復元して全体を翻訳し、実際の境界に `【原ページ】` を戻します。再現可能な処理は決定論的な文書処理で行い、規則だけでは判定しにくいレイアウトや視覚オブジェクトにはマルチモーダルモデルを利用します。

主な改善点：

- 本文、ヘッダー、フッター、脚注、後注を別々に分割・翻訳し、正しい位置へ再構築；
- 画像、地図、図、キャプション、表を文脈に基づいて配置し、無関係な著作権画像を除外可能；
- Source、翻訳単位、正確な occurrence/span に限定した用語修正；
- 難ページの回答をオブジェクト単位で非破壊的に反映；
- 原文版、対象言語版、対訳版を動的な書名・言語ファイル名で出力；
- 一時停止、再開、再起動後の続行、キャンセル、失敗時の再試行；
- 完成 PDF のプレビュー、前後ページ移動、ページ番号ジャンプ；
- 簡体字中国語、英語、フランス語、ドイツ語、日本語、スペイン語の全 30 方向を確認済み。

## 初めての使い方

1. `Bookflow-Scholar-0.8.0-rc.2-setup.exe` をインストールします。インストールしない場合は portable ZIP を展開し、`Bookflow Scholar.exe` を実行します。
2. **Create project** を選びます。PDF の作業領域とコンテキストを確定するため、先にプロジェクトが必要です。
3. プロジェクトを開き、テキスト／ビジョン Provider、モデル名、API Key を設定して保存します。Key は Windows 資格情報マネージャーに保存されます。
4. **Import PDF** を選び、原文言語と対象言語を指定します。複数 Source がある場合は、使用中の Source を明示的に選択します。
5. **Start** を選びます。進行中は一時停止、再開、キャンセル、再起動後の続行が可能です。
6. 完了後、Overview で最終 PDF を確認し、前へ、次へ、または `現在/総ページ数` で移動します。
7. 候補がある場合だけ、用語集と難ページの ZIP が出力されます。同梱の対象言語プロンプトに従って記入し、再インポートします。
8. **Open output folder** から 3 種類の成果物を開きます。

## インストールと安全

この候補版は未署名のため、Windows SmartScreen が表示される場合があります。Release ページの SHA-256 を確認するか、portable ZIP を使用してください。[LibreOffice は公式サイトからダウンロード](https://www.libreoffice.org/download/)でき、任意ですが推奨です。

公開フィードバックに機密文書、API Key、Authorization Header、個人パス、個人情報を含めないでください。無料の [GitHub 問題フォーム](https://github.com/huanghaitck/bookflow-scholar/issues/new?template=user_problem.yml) を利用してください。
