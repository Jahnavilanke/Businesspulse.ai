-- Business test: every cohort is 100% active in its acquisition month.
select * from {{ ref('mart_cohort_retention') }} where months_since_first = 0 and retention_rate <> 1
