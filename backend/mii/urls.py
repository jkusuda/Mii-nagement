from django.urls import path

from mii import views

urlpatterns = [
    path("health", views.health),
    path("settings", views.settings_view),
    path("brains", views.brains),
    path("brains/detail", views.brains_detail),
    path("models", views.models_list),
    path("islands", views.islands_view),
    path("github/repos", views.github_repos),
    path("github/branches", views.github_branches),
    path("islands/<str:island_id>", views.get_island),
    path("islands/<str:island_id>/tasks", views.create_task),
    path("islands/<str:island_id>/insights", views.get_insights),
    path("replay/<str:task_id>", views.replay),
]
