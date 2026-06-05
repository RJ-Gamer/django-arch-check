"""Tests for the N+1 serializer-risk detector."""

from __future__ import annotations

import textwrap

from django_arch_check.detectors.n1_serializer_risk import detect
from tests.conftest import ProjectBuilder


def test_serializer_method_field_with_orm_call_is_error(proj: ProjectBuilder) -> None:
    proj.write(
        "api/serializers.py",
        textwrap.dedent("""\
        from rest_framework import serializers

        class ResourceSerializer(serializers.ModelSerializer):
            likes_count = serializers.SerializerMethodField()

            def get_likes_count(self, obj):
                return obj.likes.count()
        """),
    )

    findings = detect(proj.path)

    assert len(findings) == 1
    assert findings[0].severity == "error"
    assert findings[0].file == "api/serializers.py"
    assert findings[0].code_snippet["start_line"] == 6


def test_serializer_method_field_with_context_lookup_is_clean(
    proj: ProjectBuilder,
) -> None:
    proj.write(
        "api/serializers.py",
        textwrap.dedent("""\
        from rest_framework import serializers

        class ResourceSerializer(serializers.ModelSerializer):
            likes_count = serializers.SerializerMethodField()

            def get_likes_count(self, obj):
                return self.context['likes_map'].get(obj.id, 0)
        """),
    )

    assert detect(proj.path) == []


def test_nested_serializer_without_prefetch_is_error(proj: ProjectBuilder) -> None:
    proj.write(
        "catalog/serializers.py",
        textwrap.dedent("""\
        from rest_framework import serializers

        class ResourceSerializer(serializers.ModelSerializer):
            pass

        class CategorySerializer(serializers.ModelSerializer):
            resources = ResourceSerializer(many=True)
        """),
    )
    proj.write(
        "catalog/views.py",
        textwrap.dedent("""\
        from rest_framework import viewsets
        from catalog.models import Category
        from catalog.serializers import CategorySerializer

        class CategoryViewSet(viewsets.ModelViewSet):
            queryset = Category.objects.all()
            serializer_class = CategorySerializer
        """),
    )

    findings = detect(proj.path)

    assert len(findings) == 1
    assert findings[0].severity == "error"
    assert "resources" in findings[0].message


def test_nested_serializer_with_prefetch_is_clean(proj: ProjectBuilder) -> None:
    proj.write(
        "catalog/serializers.py",
        textwrap.dedent("""\
        from rest_framework import serializers

        class ResourceSerializer(serializers.ModelSerializer):
            pass

        class CategorySerializer(serializers.ModelSerializer):
            resources = ResourceSerializer(many=True)
        """),
    )
    proj.write(
        "catalog/views.py",
        textwrap.dedent("""\
        from rest_framework import viewsets
        from catalog.models import Category
        from catalog.serializers import CategorySerializer

        class CategoryViewSet(viewsets.ModelViewSet):
            queryset = Category.objects.prefetch_related('resources').all()
            serializer_class = CategorySerializer
        """),
    )

    assert detect(proj.path) == []


def test_model_property_used_as_source_with_orm_call_is_error(
    proj: ProjectBuilder,
) -> None:
    proj.write(
        "hospitals/models.py",
        textwrap.dedent("""\
        from django.db import models

        class Hospital(models.Model):
            @property
            def active_resource_count(self):
                return self.resources.filter(active=True).count()
        """),
    )
    proj.write(
        "hospitals/serializers.py",
        textwrap.dedent("""\
        from rest_framework import serializers
        from hospitals.models import Hospital

        class HospitalSerializer(serializers.ModelSerializer):
            active_resource_count = serializers.IntegerField(source='active_resource_count')

            class Meta:
                model = Hospital
        """),
    )

    findings = detect(proj.path)

    assert len(findings) == 1
    assert findings[0].severity == "error"
    assert findings[0].file == "hospitals/models.py"
    assert findings[0].code_snippet["lines"][0].strip() == "@property"


def test_bare_queryset_with_relational_serializer_is_warning(
    proj: ProjectBuilder,
) -> None:
    proj.write(
        "resources/serializers.py",
        textwrap.dedent("""\
        from rest_framework import serializers

        class ResourceSerializer(serializers.ModelSerializer):
            owner = serializers.PrimaryKeyRelatedField(read_only=True)
        """),
    )
    proj.write(
        "resources/views.py",
        textwrap.dedent("""\
        from rest_framework import viewsets
        from resources.models import Resource
        from resources.serializers import ResourceSerializer

        class ResourceViewSet(viewsets.ModelViewSet):
            queryset = Resource.objects.all()
            serializer_class = ResourceSerializer
        """),
    )

    findings = detect(proj.path)

    assert len(findings) == 1
    assert findings[0].severity == "warning"
    assert findings[0].file == "resources/views.py"


def test_relational_serializer_with_select_related_is_clean(
    proj: ProjectBuilder,
) -> None:
    proj.write(
        "resources/serializers.py",
        textwrap.dedent("""\
        from rest_framework import serializers

        class ResourceSerializer(serializers.ModelSerializer):
            owner = serializers.PrimaryKeyRelatedField(read_only=True)
        """),
    )
    proj.write(
        "resources/views.py",
        textwrap.dedent("""\
        from rest_framework import viewsets
        from resources.models import Resource
        from resources.serializers import ResourceSerializer

        class ResourceViewSet(viewsets.ModelViewSet):
            queryset = Resource.objects.select_related('owner').all()
            serializer_class = ResourceSerializer
        """),
    )

    assert detect(proj.path) == []
