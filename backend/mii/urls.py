from django.urls import path

from mii import views

urlpatterns = [
    path("health", views.health),
    path("settings", views.settings_view),
    path("brains", views.brains),
    path("brains/detail", views.brains_detail),
    path("gifts", views.gifts),
    path("models", views.models_list),
    path("islands", views.islands_view),
    path("github/repos", views.github_repos),
    path("github/branches", views.github_branches),
    path("islands/<str:island_id>", views.get_island),
    path("islands/<str:island_id>/tasks", views.tasks_view),
    path("islands/<str:island_id>/tasks/<str:task_id>", views.get_task),
    path("islands/<str:island_id>/insights", views.get_insights),
    path("islands/<str:island_id>/events", views.get_events),
    path("islands/<str:island_id>/layout", views.layout_view),
    path("replay/<str:task_id>", views.replay),
]
