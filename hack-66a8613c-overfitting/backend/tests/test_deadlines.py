from datetime import date
import unittest

from backend.app.processing.deadlines import normalize_deadline


class DeadlineTests(unittest.TestCase):
    def test_russian_and_kazakh_calendar_rules(self):
        cases = {'сегодня': '2026-09-23', 'бүгін': '2026-09-23',
                 'завтра': '2026-09-24', 'ертең': '2026-09-24',
                 'послезавтра': '2026-09-25', 'бүрсігүні': '2026-09-25',
                 'до конца недели': '2026-09-27', 'аптаның соңына дейін': '2026-09-27',
                 'в пятницу': '2026-09-25', 'жұма күні': '2026-09-25',
                 'до пятницы': '2026-09-25', 'жұмаға дейін': '2026-09-25',
                 'в среду': '2026-09-23', 'келесі сәрсенбі': '2026-09-30',
                 'в следующую среду': '2026-09-30', 'понедельник': '2026-09-28',
                 'через 3 дня': '2026-09-26', 'через 2 недели': '2026-10-07',
                 '3 күннен кейін': '2026-09-26', '2 аптадан кейін': '2026-10-07'}
        for phrase, expected in cases.items():
            with self.subTest(phrase=phrase):
                self.assertEqual(normalize_deadline(phrase, '2026-09-23', 'Asia/Qyzylorda'), date.fromisoformat(expected))

    def test_missing_context_ambiguous_and_invalid_values(self):
        for phrase in ('не указан', 'в пятницу или понедельник', 'завтра вечером или позже',
                       'через несколько дней', 'келесі аптада', 'через -1 дней', 'через 999999999 дней'):
            self.assertIsNone(normalize_deadline(phrase, '2026-09-23', 'Asia/Qyzylorda'))
        for day, zone in [(None, 'UTC'), ('2026-09-23', None), ('invalid', 'UTC'), ('2026-09-23', 'bad-zone')]:
            self.assertIsNone(normalize_deadline('завтра', day, zone))

    def test_year_leap_and_overflow(self):
        self.assertEqual(normalize_deadline('завтра', '2024-02-28', 'UTC'), date(2024, 2, 29))
        self.assertEqual(normalize_deadline('завтра', '2026-12-31', 'UTC'), date(2027, 1, 1))
        self.assertIsNone(normalize_deadline('завтра', '9999-12-31', 'UTC'))
