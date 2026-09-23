import unittest

from backend.app.processing.alignment import merge_transcript
from backend.app.processing.contracts import AsrSegment, DiarizationTurn


class AlignmentTests(unittest.TestCase):
    def segment(self, start, end):
        return AsrSegment(start=start, end=end, text='unit fixture')

    def turn(self, start, end, speaker):
        return DiarizationTurn(start=start, end=end, speaker=speaker)

    def test_maximum_intersection_and_neutral_names(self):
        speakers, utterances = merge_transcript('m', [self.segment(0, 4), self.segment(4, 6)],
                                                [self.turn(0, 1, 'B'), self.turn(1, 6, 'A')])
        self.assertEqual([speaker.name for speaker in speakers], ['Спикер 1', 'Спикер 2'])
        self.assertEqual([speaker.label for speaker in speakers], ['speaker_1', 'speaker_2'])
        self.assertEqual(utterances[0].speaker_id, speakers[1].id)
        self.assertTrue(utterances[0].requires_review)
        self.assertFalse(utterances[1].requires_review)

    def test_gaps_ties_and_overlapping_duplicate_turns(self):
        speakers, utterances = merge_transcript('m', [self.segment(0, 4), self.segment(7, 9)],
                                                [self.turn(0, 2, 'A'), self.turn(0, 2, 'A'), self.turn(2, 4, 'B')])
        self.assertEqual(utterances[0].speaker_id, speakers[0].id)
        self.assertTrue(utterances[0].requires_review)
        self.assertIsNone(utterances[1].speaker_id)
        self.assertTrue(utterances[1].requires_review)

    def test_empty_diarization_never_invents_speaker(self):
        speakers, utterances = merge_transcript('m', [self.segment(1, 2)], [])
        self.assertEqual(speakers, [])
        self.assertIsNone(utterances[0].speaker_id)
        self.assertTrue(utterances[0].requires_review)
