# Opportunity Desk

A GitHub-hosted opportunity board for international visa-sponsorship jobs and scholarships.

## Included

- Visa-sponsorship job searches across multiple countries and common occupations.
- Skilled/construction roles such as electrician, plumber, welder, carpenter, masonry, HVAC, construction labour, mechanics and technicians.
- Care, healthcare, cleaning, hospitality, warehouse, driving, agriculture, security and IT/finance searches.
- Scholarship tracking for undergraduate, master's and PhD opportunities.
- Funding, IELTS, country and application-status filters.
- Carejobz and other specialist external job-source links.
- Automatic GitHub Actions updates every 6 hours.
- Existing records are preserved and de-duplicated.

## GitHub secrets

Add these repository secrets if you want Adzuna jobs to populate automatically:

- `ADZUNA_APP_ID`
- `ADZUNA_APP_KEY`

Arbeitnow's native visa-sponsorship feed does not require an Adzuna key.

## Important

The site does not claim that every job is sponsored. A listing is marked `SPONSORED` only when the source advert contains sponsorship evidence or comes from a source's native sponsorship filter. Always verify the employer's current sponsorship policy before applying.

The repository includes a small set of current scholarship seed records so the deployed site is not blank before the first successful scraper run. The GitHub Action keeps those records and adds new ones.
