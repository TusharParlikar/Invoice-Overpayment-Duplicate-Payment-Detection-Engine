# Test receipts

Four receipts from the **test split** of *Find it again! A Receipt Dataset for Document Forgery Detection*
(B. Martínez Tornés et al., ICDAR 2023, L3i, University of La Rochelle,
[dataset page](https://l3i-share.univ-lr.fr/2023Finditagain/index.html)), published for research use. The tamper model
never trained on them. File names are the dataset's own.

`expected.csv` holds what `tests/test_receipts.py` must get from each: the fields OCR + the parser read (the same as the
parser gets from the dataset's own transcription), and whether the tamper model flags it (`forged` = 1: edited by the
dataset's authors). Two are genuine, two forged.
