from django.urls import path

from mii import views

urlpatterns = [
    path("health", views.health),
    path("meta", views.meta),
    path("settings", views.settings_view),
    path("brains", views.brains),
    path("brains/detail", views.brains_detail),
    path("brains/<str:brain>/models", views.brain_models),
    path("gifts", views.gifts_view),
    path("gifts/search", views.gift_search),
    path("gifts/<str:gift>", views.gift_view),
    path("usage", views.usage_view),
    path("models", views.models_list),
    path("islands", views.islands_view),
    path("github/repos", views.github_repos),
    path("github/branches", views.github_branches),
    path("islands/<str:island_id>", views.island_view),
    path("islands/<str:island_id>/residents/<str:resident_id>", views.resident_view),
    path("islands/<str:island_id>/tasks", views.tasks_view),
    path("islands/<str:island_id>/tasks/<str:task_id>", views.get_task),
    path("islands/<str:island_id>/tasks/<str:task_id>/cancel", views.cancel_task),
    path("islands/<str:island_id>/tasks/<str:task_id>/merge", views.merge_task),
    path("islands/<str:island_id>/tasks/<str:task_id>/merge-pr", views.merge_pr),
    path("islands/<str:island_id>/issues", views.island_issues),
    path("islands/<str:island_id>/insights", views.get_insights),
    path("islands/<str:island_id>/events", views.get_events),
    path("islands/<str:island_id>/talks", views.talks_view),
    path("islands/<str:island_id>/layout", views.layout_view),
]
