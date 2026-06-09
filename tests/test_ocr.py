from embykeeper.ocr import OCRService


def test_gif_frame_indices_handles_single_frame():
    assert OCRService._gif_frame_indices(1) == [0]


def test_gif_frame_indices_uses_all_frames_when_short():
    assert OCRService._gif_frame_indices(3) == [0, 1, 2]


def test_gif_frame_indices_samples_up_to_five_frames():
    assert OCRService._gif_frame_indices(10) == [0, 2, 4, 6, 9]
