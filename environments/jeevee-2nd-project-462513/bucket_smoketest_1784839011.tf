resource "google_storage_bucket" "smoketest_1784839011" {
  name     = "smoketest-1784839011"
  project  = "jeevee-2nd-project-462513"
  location = "asia-south1"

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true
  }
}
