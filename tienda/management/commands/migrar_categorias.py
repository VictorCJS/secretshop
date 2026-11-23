from django.core.management.base import BaseCommand
from django.db import transaction

from tienda.models import Franquicia, Producto, TipoProducto

# Nombres que se consideran "tipo de producto" al convertir las categorías antiguas.
TIPOS_CONOCIDOS = {"tcg", "figura", "figuras", "ropa", "otros"}


def obtener(modelo, nombre):
    nombre = " ".join(nombre.split())
    existente = modelo.objects.filter(nombre__iexact=nombre).first()
    return existente or modelo.objects.create(nombre=nombre)


def separar(categoria):
    """Devuelve (franquicia, tipo) a partir de una categoría antigua."""
    if categoria.padre_id:
        principal, sub = categoria.padre.nombre, categoria.nombre
        if sub.lower() in TIPOS_CONOCIDOS and principal.lower() not in TIPOS_CONOCIDOS:
            return principal, sub
        # Por defecto: la subcategoría es la franquicia y la principal el tipo.
        return sub, principal
    if categoria.nombre.lower() in TIPOS_CONOCIDOS:
        return None, categoria.nombre
    return categoria.nombre, "Otros"


class Command(BaseCommand):
    help = "Convierte las categorías antiguas en franquicia y tipo de producto."

    def add_arguments(self, parser):
        parser.add_argument(
            "--simular", action="store_true",
            help="Muestra lo que haría, sin guardar nada.",
        )

    @transaction.atomic
    def handle(self, *args, **opciones):
        pendientes = Producto.objects.filter(
            categoria__isnull=False, tipo__isnull=True
        ).select_related("categoria__padre")

        total = 0
        for producto in pendientes:
            nombre_franquicia, nombre_tipo = separar(producto.categoria)
            self.stdout.write(
                f"{producto.nombre}: {producto.categoria} -> "
                f"franquicia={nombre_franquicia or '—'}, tipo={nombre_tipo}"
            )
            producto.franquicia = obtener(Franquicia, nombre_franquicia) if nombre_franquicia else None
            producto.tipo = obtener(TipoProducto, nombre_tipo)
            producto.save(update_fields=["franquicia", "tipo"])
            total += 1

        if opciones["simular"]:
            transaction.set_rollback(True)
            self.stdout.write(self.style.WARNING(f"Simulación: {total} producto(s). No se guardó nada."))
        else:
            self.stdout.write(self.style.SUCCESS(f"{total} producto(s) convertidos."))