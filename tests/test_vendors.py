from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.parsers import classify_service_link
from src.vendors import detect_vendor


class VendorDetectionTests(unittest.TestCase):
    def test_detect_governmentjobs_vendor_by_domain(self) -> None:
        vendor, confidence = detect_vendor("https://www.governmentjobs.com/careers/chesterct")
        self.assertEqual(vendor, "GovernmentJobs")
        self.assertGreaterEqual(confidence, 0.9)

    def test_classify_governmentjobs_as_jobs(self) -> None:
        category, confidence = classify_service_link(
            "https://www.governmentjobs.com/jobs/chesterct",
            "jobs url",
        )
        self.assertEqual(category, "jobs")
        self.assertGreater(confidence, 0.0)


if __name__ == "__main__":
    unittest.main()
