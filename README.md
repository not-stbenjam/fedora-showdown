# FedoraBench

A vibe bench for AI coding models. Every model gets the same prompt — generate a single-file HTML page with a 3D spinning Red Hat-style fedora that dissolves into particles on hover — and the results are collected so you can see how each one vibes.

Browse models in the sidebar, rate them with stars, or use grid view to compare up to four at once. Community ratings are aggregated from votes.

A GitHub Action automatically detects new models on OpenRouter and opens one PR per model. When OpenRouter advertises model-specific reasoning efforts, the action runs those efforts in parallel and adds each successful result as a separate commit and comparison variant. Failed efforts are reported in the partial PR instead of discarding successful runs. Models without selectable efforts receive one default run.
