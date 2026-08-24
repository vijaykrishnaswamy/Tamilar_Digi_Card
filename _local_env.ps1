# Mirrors the env of Cloud Run revision wallet-service-00008-xq7 so jobs/csv_job.py
# behaves locally as it does in the service. Secrets are NOT here - they are read
# from Secret Manager using GOOGLE_APPLICATION_CREDENTIALS.
#
# Usage:  . .\_local_env.ps1

$env:GOOGLE_APPLICATION_CREDENTIALS = "$env:USERPROFILE\.wallet\wallet-sa.json"

$env:GCP_PROJECT             = "tamilar-wallet-au-prod"
$env:SERVICE_BASE_URL        = "https://wallet-service-373200174749.australia-southeast1.run.app"
$env:GOOGLE_ISSUER_ID        = "3388000000023166651"
$env:GOOGLE_CLASS_SUFFIX     = "membership"
$env:ORG_NAME                = "Tamilar Inc"
$env:SUPPORT_EMAIL           = "vanakkam@tamilar.org.au"
$env:EMAIL_SENDER            = "vanakkam@tamilar.org.au"
$env:MAX_DEVICES_PER_MEMBER  = "2"
$env:APPLE_TEAM_ID           = "V82AL469J5"
$env:APPLE_PASS_TYPE_ID      = "pass.au.org.tamilar.membership"
$env:APPLE_APNS_KEY_ID       = "8566PH4G57"
$env:APPLE_APNS_HOST         = "api.push.apple.com"
$env:PHOTO_BASE_URL          = "https://raw.githubusercontent.com/vijaykrishnaswamy/Tamilar_Logo/main"
$env:PHOTO_PREFIX            = "member-photos/"
$env:PHOTO_URL_TTL_DAYS      = "7"

Write-Host "local env set for tamilar-wallet-au-prod (SERVICE_BASE_URL=$env:SERVICE_BASE_URL)"
