import json
import uuid
import hashlib
import hmac
from decimal import Decimal
from urllib import error as urlerror, request as urlrequest
from django.db.models import Count, Q
from urllib.parse import urlencode
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from functools import wraps
from django.core.exceptions import PermissionDenied
from django.db.models import Count
from decimal import Decimal, InvalidOperation
from django.core.exceptions import ValidationError
from .models import registrar_venta_fisica
from datetime import date

from django.db.models import Count, Sum
from django.utils import timezone

from .forms import BannerForm, CategoriaForm, FranquiciaForm, PedidoForm, ProductoForm, TipoForm
from .models import (
    Banner, Categoria, Franquicia, Pedido, PedidoItem, Producto, TipoProducto,
)

def lista_productos(request):
    productos = Producto.objects.filter(activo=True).select_related("franquicia", "tipo")

    def a_numero(valor):
        return int(valor) if valor.isdigit() else None

    franquicia_actual = None
    tipo_actual = None
    id_franquicia = a_numero(request.GET.get("franquicia", ""))
    id_tipo = a_numero(request.GET.get("tipo", ""))
    if id_franquicia:
        franquicia_actual = Franquicia.objects.filter(pk=id_franquicia).first()
    if id_tipo:
        tipo_actual = TipoProducto.objects.filter(pk=id_tipo).first()
    busqueda = request.GET.get("q", "").strip()

    if franquicia_actual:
        productos = productos.filter(franquicia=franquicia_actual)
    if tipo_actual:
        productos = productos.filter(tipo=tipo_actual)
    if busqueda:
        productos = productos.filter(nombre__icontains=busqueda)

    def url(franquicia=franquicia_actual, tipo=tipo_actual):
        """Dirección que conserva los filtros actuales y cambia el indicado."""
        params = {}
        if franquicia:
            params["franquicia"] = franquicia.pk
        if tipo:
            params["tipo"] = tipo.pk
        if busqueda:
            params["q"] = busqueda
        return ("?" + urlencode(params)) if params else reverse("lista_productos")

    tipos_menu = [
        {"nombre": t.nombre, "url": url(tipo=t), "activo": t == tipo_actual}
        for t in TipoProducto.objects.filter(productos__activo=True).distinct()
    ]
    franquicias_menu = [
        {"nombre": f.nombre, "url": url(franquicia=f), "activo": f == franquicia_actual}
        for f in Franquicia.objects.filter(productos__activo=True).distinct()
    ]

    # Los banners solo se muestran en el catálogo completo, sin filtros ni búsqueda.
    banners = []
    if not franquicia_actual and not tipo_actual and not busqueda:
        banners = Banner.objects.filter(activo=True)

    return render(request, "tienda/lista.html", {
        "productos": productos,
        "banners": banners,
        "busqueda": busqueda,
        "franquicia_actual": franquicia_actual,
        "tipo_actual": tipo_actual,
        "tipos_menu": tipos_menu,
        "franquicias_menu": franquicias_menu,
        "url_todos_tipos": url(tipo=None),
        "url_todas_franquicias": url(franquicia=None),
    })

def detalle_producto(request, pk):
    producto = get_object_or_404(Producto, pk=pk, activo=True)
    return render(request, "tienda/detalle.html", {"producto": producto})


def registro(request):
    if request.method == "POST":
        form = UserCreationForm(request.POST)
        if form.is_valid():
            usuario = form.save()
            login(request, usuario)
            return redirect("lista_productos")
    else:
        form = UserCreationForm()
    return render(request, "registration/registro.html", {"form": form})


def obtener_items_carrito(carrito):
    productos = Producto.objects.filter(pk__in=carrito.keys(), activo=True)
    items = []
    total = 0
    for producto in productos:
        cantidad = carrito[str(producto.pk)]
        subtotal = producto.precio * cantidad
        total += subtotal
        items.append({
            "producto": producto,
            "cantidad": cantidad,
            "subtotal": subtotal,
        })
    return items, total


@require_POST
def agregar_al_carrito(request, pk):
    producto = get_object_or_404(Producto, pk=pk, activo=True)
    carrito = request.session.get("carrito", {})
    cantidad_actual = carrito.get(str(pk), 0)
    if cantidad_actual < producto.stock:
        carrito[str(pk)] = cantidad_actual + 1
        request.session["carrito"] = carrito
    return redirect("ver_carrito")


@require_POST
def quitar_del_carrito(request, pk):
    carrito = request.session.get("carrito", {})
    carrito.pop(str(pk), None)
    request.session["carrito"] = carrito
    return redirect("ver_carrito")


def ver_carrito(request):
    carrito = request.session.get("carrito", {})
    items, total = obtener_items_carrito(carrito)
    return render(request, "tienda/carrito.html", {"items": items, "total": total})


@login_required
def checkout(request):
    carrito = request.session.get("carrito", {})
    items, total = obtener_items_carrito(carrito)
    if not items:
        return redirect("ver_carrito")

    if request.method == "POST":
        form = PedidoForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                productos = Producto.objects.select_for_update().filter(
                    pk__in=carrito.keys()
                )
                productos_por_id = {p.pk: p for p in productos}

                total_real = 0
                for item in items:
                    producto = productos_por_id.get(item["producto"].pk)
                    if (
                        producto is None
                        or not producto.activo
                        or producto.stock < item["cantidad"]
                    ):
                        messages.error(
                            request,
                            f"No hay suficiente stock de {item['producto'].nombre}.",
                        )
                        return redirect("ver_carrito")
                    total_real += producto.precio * item["cantidad"]

                pedido = form.save(commit=False)
                pedido.usuario = request.user
                pedido.total = total_real
                pedido.save()

                for item in items:
                    producto = productos_por_id[item["producto"].pk]
                    PedidoItem.objects.create(
                        pedido=pedido,
                        producto=producto,
                        nombre=producto.nombre,
                        precio=producto.precio,
                        cantidad=item["cantidad"],
                    )
                    producto.stock -= item["cantidad"]
                    producto.save(update_fields=["stock"])

            request.session["carrito"] = {}
            return redirect("pagar_pedido", pk=pedido.pk)
    else:
        form = PedidoForm(initial={"nombre": request.user.get_full_name()})

    return render(request, "tienda/checkout.html", {
        "form": form,
        "items": items,
        "total": total,
    })


@login_required
def pedido_confirmado(request, pk):
    pedido = get_object_or_404(Pedido, pk=pk, usuario=request.user)
    return render(request, "tienda/pedido_confirmado.html", {"pedido": pedido})


@login_required
def mis_pedidos(request):
    pedidos = Pedido.objects.filter(usuario=request.user).prefetch_related("items")
    return render(request, "tienda/mis_pedidos.html", {"pedidos": pedidos})


# ---------- Pagos con Mercado Pago (API de Orders) ----------

MP_API = "https://api.mercadopago.com"


def mp_request(metodo, ruta, cuerpo=None):
    """Llama a la API de Mercado Pago. Devuelve (código_http, respuesta_json)."""
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    req = urlrequest.Request(MP_API + ruta, data=datos, method=metodo)
    req.add_header("Authorization", f"Bearer {settings.MERCADOPAGO_ACCESS_TOKEN}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    if metodo == "POST":
        req.add_header("X-Idempotency-Key", str(uuid.uuid4()))
    try:
        with urlrequest.urlopen(req, timeout=20) as respuesta:
            return respuesta.status, json.loads(respuesta.read().decode() or "{}")
    except urlerror.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except ValueError:
            return e.code, {}
    except (urlerror.URLError, TimeoutError):
        return 0, {}


def sincronizar_pago(order_id):
    """Consulta la orden a Mercado Pago y, si está pagada, marca el pedido como pagado."""
    estado, orden = mp_request("GET", f"/v1/orders/{order_id}")
    if estado != 200:
        return None

    try:
        pedido = Pedido.objects.get(pk=int(orden.get("external_reference")))
    except (TypeError, ValueError, Pedido.DoesNotExist):
        return None
    if orden.get("id") != pedido.mp_order_id:
        return None

    pagada = (
        orden.get("status") == "processed"
        and orden.get("status_detail") == "accredited"
        and Decimal(str(orden.get("total_paid_amount") or "0")) >= pedido.total
    )
    if pagada and pedido.estado in ("pendiente", "por_verificar"):
        pedido.estado = "pagado"
        pedido.pago_id = str(order_id)
        pedido.save(update_fields=["estado", "pago_id"])
    return pedido


@login_required
def pagar_pedido(request, pk):
    pedido = get_object_or_404(Pedido, pk=pk, usuario=request.user)
    if pedido.estado not in ("pendiente", "por_verificar"):
        return redirect("pedido_confirmado", pk=pedido.pk)

    if not settings.MERCADOPAGO_ACCESS_TOKEN:
        messages.error(request, "El pago en línea no está configurado todavía.")
        return redirect("pedido_confirmado", pk=pedido.pk)

    # Si ya existe una orden abierta para este pedido, se reutiliza.
    if pedido.mp_order_id:
        estado, orden = mp_request("GET", f"/v1/orders/{pedido.mp_order_id}")
        if estado == 200 and orden.get("status") == "created" and orden.get("checkout_url"):
            return redirect(orden["checkout_url"])

    items = list(pedido.items.all())
    total = sum((i.precio * i.cantidad for i in items), Decimal("0"))
    retorno = f"{settings.SITE_URL}{reverse('pago_retorno')}"

    config_online = {
        "success_url": retorno,
        "failure_url": retorno,
        "pending_url": retorno,
    }
    if settings.SITE_URL.startswith("https://"):
        config_online["auto_return"] = "approved"

    cuerpo = {
        "type": "online",
        "processing_mode": "manual",
        "total_amount": f"{total:.2f}",
        "external_reference": str(pedido.pk),
        "items": [
            {
                "title": i.nombre,
                "unit_price": f"{i.precio:.2f}",
                "quantity": i.cantidad,
            }
            for i in items
        ],
        "config": {"online": config_online},
    }
    if settings.MP_PAYER_EMAIL:
        cuerpo["payer"] = {"email": settings.MP_PAYER_EMAIL}
    estado, orden = mp_request("POST", "/v1/orders", cuerpo)
    if estado not in (200, 201) or not orden.get("checkout_url"):
        print("Mercado Pago respondió:", estado, orden)  # solo para desarrollo
        messages.error(request, "No pudimos iniciar el pago. Intenta de nuevo.")
        return redirect("pedido_confirmado", pk=pedido.pk)

    pedido.mp_order_id = orden["id"]
    pedido.save(update_fields=["mp_order_id"])
    return redirect(orden["checkout_url"])


@login_required
def pago_retorno(request):
    # El regreso solo sirve para ubicar el pedido; el estado real se consulta a Mercado Pago.
    try:
        pedido = Pedido.objects.get(
            pk=int(request.GET.get("external_reference")), usuario=request.user
        )
    except (TypeError, ValueError, Pedido.DoesNotExist):
        messages.warning(request, "No pudimos identificar tu pedido.")
        return redirect("mis_pedidos")

    if pedido.mp_order_id:
        sincronizar_pago(pedido.mp_order_id)
    return redirect("pedido_confirmado", pk=pedido.pk)


@csrf_exempt
def firma_webhook_valida(request, data_id):
    secreto = settings.MP_WEBHOOK_SECRET
    if not secreto:
        # Sin secreto solo se permite en desarrollo.
        return settings.DEBUG

    x_signature = request.headers.get("x-signature", "")
    x_request_id = request.headers.get("x-request-id", "")
    partes = dict(
        p.strip().split("=", 1) for p in x_signature.split(",") if "=" in p
    )
    ts = partes.get("ts", "")
    v1 = partes.get("v1", "")
    if not ts or not v1:
        return False

    plantilla = ""
    if data_id:
        plantilla += f"id:{str(data_id).lower()};"
    if x_request_id:
        plantilla += f"request-id:{x_request_id};"
    plantilla += f"ts:{ts};"

    calculada = hmac.new(
        secreto.encode(), plantilla.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(calculada, v1)


@csrf_exempt
def mp_webhook(request):
    if request.method != "POST":
        return HttpResponse(status=405)

    data_id_url = request.GET.get("data.id")
    if not firma_webhook_valida(request, data_id_url):
        return HttpResponse(status=401)

    order_id = data_id_url
    if not order_id:
        try:
            cuerpo = json.loads(request.body or b"{}")
            order_id = (cuerpo.get("data") or {}).get("id")
        except ValueError:
            order_id = None

    if order_id:
        sincronizar_pago(order_id)
    return HttpResponse(status=200)

def solo_admin(vista):
    """Permite el acceso solo a usuarios con sesión y permiso de staff."""
    @wraps(vista)
    @login_required
    def envoltura(request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied
        return vista(request, *args, **kwargs)
    return envoltura


@solo_admin
def panel_inicio(request):
    conteos = dict(Pedido.objects.values_list("estado").annotate(total=Count("id")))
    return render(request, "tienda/panel_inicio.html", {
        "seccion": "inicio",
        "por_enviar": conteos.get("pagado", 0),
        "en_camino": conteos.get("enviado", 0) + conteos.get("en_transito", 0),
        "pendientes": conteos.get("pendiente", 0),
        "bajo_stock": Producto.objects.filter(activo=True, stock__lte=2).count(),
    })


@solo_admin
def panel_pedidos(request):
    estado = request.GET.get("estado", Pedido.Estado.PAGADO)
    if estado != "todos" and estado not in Pedido.Estado.values:
        estado = Pedido.Estado.PAGADO

    pedidos = Pedido.objects.select_related("usuario").prefetch_related("items")
    if estado != "todos":
        pedidos = pedidos.filter(estado=estado)

    conteos = dict(Pedido.objects.values_list("estado").annotate(total=Count("id")))
    pestanas = [
        (valor, etiqueta, conteos.get(valor, 0))
        for valor, etiqueta in Pedido.Estado.choices
    ]

    return render(request, "tienda/panel_pedidos.html", {
        "seccion": "pedidos",
        "pedidos": pedidos,
        "estado": str(estado),
        "pestanas": pestanas,
        "estados": Pedido.Estado.choices,
    })


@solo_admin
@require_POST
def panel_cambiar_estado(request, pk):
    pedido = get_object_or_404(Pedido, pk=pk)
    nuevo = request.POST.get("estado")
    guia = request.POST.get("numero_guia", "").strip()[:100]

    if pedido.estado == Pedido.Estado.CANCELADO:
        messages.warning(request, "Un pedido cancelado ya no se puede modificar.")
    elif nuevo in Pedido.Estado.values:
        pedido.numero_guia = guia
        if nuevo == Pedido.Estado.CANCELADO:
            pedido.save(update_fields=["numero_guia"])
            pedido.cancelar()
            messages.success(request, f"Pedido #{pedido.pk} cancelado y stock devuelto.")
        else:
            pedido.estado = nuevo
            pedido.save(update_fields=["estado", "numero_guia"])
            messages.success(request, f"Pedido #{pedido.pk} actualizado.")

    volver = request.POST.get("volver", "todos")
    if volver != "todos" and volver not in Pedido.Estado.values:
        volver = "todos"
    return redirect(f"{reverse('panel_pedidos')}?estado={volver}")


@solo_admin
def panel_productos(request):
    productos = Producto.objects.select_related("franquicia", "tipo").order_by("-creado")
    busqueda = request.GET.get("q", "").strip()
    if busqueda:
        productos = productos.filter(nombre__icontains=busqueda)
    return render(request, "tienda/panel_productos.html", {
        "seccion": "productos",
        "productos": productos,
        "busqueda": busqueda,
    })

@solo_admin
def panel_producto_form(request, pk=None):
    producto = get_object_or_404(Producto, pk=pk) if pk else None
    if request.method == "POST":
        form = ProductoForm(request.POST, request.FILES, instance=producto)
        if form.is_valid():
            form.save()
            messages.success(request, "Producto guardado.")
            return redirect("panel_productos")
    else:
        form = ProductoForm(instance=producto)
    return render(request, "tienda/panel_producto_form.html", {
        "seccion": "productos",
        "form": form,
        "producto": producto,
        "franquicias": list(Franquicia.objects.values_list("nombre", flat=True)),
        "tipos": list(TipoProducto.objects.values_list("nombre", flat=True)),
    })
@solo_admin
@require_POST
def panel_producto_activar(request, pk):
    producto = get_object_or_404(Producto, pk=pk)
    producto.activo = not producto.activo
    producto.save(update_fields=["activo"])
    messages.success(
        request,
        f"{producto.nombre} ahora está {'visible' if producto.activo else 'oculto'} en la tienda.",
    )
    return redirect("panel_productos")


@solo_admin
def panel_categorias(request):
    if request.method == "POST":
        form = CategoriaForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Categoría guardada.")
            return redirect("panel_categorias")
    else:
        form = CategoriaForm()
    padres = (
        Categoria.objects.filter(padre__isnull=True)
        .prefetch_related("subcategorias")
        .annotate(total=Count("productos"))
    )
    return render(request, "tienda/panel_categorias.html", {
        "seccion": "categorias",
        "form": form,
        "padres": padres,
    })
@login_required
@require_POST
def cancelar_mi_pedido(request, pk):
    pedido = get_object_or_404(Pedido, pk=pk, usuario=request.user)
    if pedido.puede_cancelar:
        pedido.cancelar()
        messages.success(request, f"Pedido #{pedido.pk} cancelado.")
    else:
        messages.warning(request, "Solo se pueden cancelar pedidos pendientes de pago.")
    return redirect("mis_pedidos")


@login_required
@require_POST
def solicitar_cancelacion(request, pk):
    pedido = get_object_or_404(Pedido, pk=pk, usuario=request.user)
    if pedido.puede_solicitar_cancelacion:
        pedido.cancelacion_solicitada = True
        pedido.save(update_fields=["cancelacion_solicitada"])
        messages.success(
            request,
            "Recibimos tu solicitud de cancelación. Te contactaremos para el reembolso.",
        )
    return redirect("mis_pedidos")


@login_required
def editar_envio(request, pk):
    pedido = get_object_or_404(Pedido, pk=pk, usuario=request.user)
    if not pedido.puede_editar_envio:
        messages.warning(request, "Este pedido ya no se puede modificar.")
        return redirect("mis_pedidos")

    if request.method == "POST":
        form = PedidoForm(request.POST, instance=pedido)
        if form.is_valid():
            cambios = form.save(commit=False)
            cambios.save(update_fields=list(form.fields))
            messages.success(request, "Datos de envío actualizados.")
            return redirect("mis_pedidos")
    else:
        form = PedidoForm(instance=pedido)
    return render(request, "tienda/editar_envio.html", {"form": form, "pedido": pedido})


@login_required
@require_POST
def cancelar_mi_pedido(request, pk):
    pedido = get_object_or_404(Pedido, pk=pk, usuario=request.user)
    if pedido.puede_cancelar:
        pedido.cancelar()
        messages.success(request, f"Pedido #{pedido.pk} cancelado.")
    else:
        messages.warning(request, "Solo se pueden cancelar pedidos pendientes de pago.")
    return redirect("mis_pedidos")


@login_required
@require_POST
def solicitar_cancelacion(request, pk):
    pedido = get_object_or_404(Pedido, pk=pk, usuario=request.user)
    if pedido.puede_solicitar_cancelacion:
        pedido.cancelacion_solicitada = True
        pedido.save(update_fields=["cancelacion_solicitada"])
        messages.success(
            request,
            "Recibimos tu solicitud de cancelación. Te contactaremos para el reembolso.",
        )
    return redirect("mis_pedidos")


@login_required
def editar_envio(request, pk):
    pedido = get_object_or_404(Pedido, pk=pk, usuario=request.user)
    if not pedido.puede_editar_envio:
        messages.warning(request, "Este pedido ya no se puede modificar.")
        return redirect("mis_pedidos")

    if request.method == "POST":
        form = PedidoForm(request.POST, instance=pedido)
        if form.is_valid():
            cambios = form.save(commit=False)
            cambios.save(update_fields=list(form.fields))
            messages.success(request, "Datos de envío actualizados.")
            return redirect("mis_pedidos")
    else:
        form = PedidoForm(instance=pedido)
    return render(request, "tienda/editar_envio.html", {"form": form, "pedido": pedido})


@login_required
@require_POST
def repetir_pedido(request, pk):
    pedido = get_object_or_404(Pedido, pk=pk, usuario=request.user)
    carrito = request.session.get("carrito", {})
    agregados = 0
    for item in pedido.items.select_related("producto"):
        producto = item.producto
        if producto and producto.activo and producto.stock > 0:
            clave = str(producto.pk)
            carrito[clave] = min(carrito.get(clave, 0) + item.cantidad, producto.stock)
            agregados += 1
    request.session["carrito"] = carrito
    if agregados == 0:
        messages.warning(request, "Esos productos ya no están disponibles.")
        return redirect("mis_pedidos")
    messages.success(request, "Agregamos los productos a tu carrito. Ajusta lo que necesites.")
    return redirect("ver_carrito")
@solo_admin
def panel_banners(request):
    return render(request, "tienda/panel_banners.html", {
        "seccion": "banners",
        "banners": Banner.objects.all(),
    })


@solo_admin
def panel_banner_form(request, pk=None):
    banner = get_object_or_404(Banner, pk=pk) if pk else None
    if request.method == "POST":
        form = BannerForm(request.POST, request.FILES, instance=banner)
        if form.is_valid():
            form.save()
            messages.success(request, "Banner guardado.")
            return redirect("panel_banners")
    else:
        form = BannerForm(instance=banner)
    return render(request, "tienda/panel_banner_form.html", {
        "seccion": "banners",
        "form": form,
        "banner": banner,
    })


@solo_admin
@require_POST
def panel_banner_activar(request, pk):
    banner = get_object_or_404(Banner, pk=pk)
    banner.activo = not banner.activo
    banner.save(update_fields=["activo"])
    messages.success(request, f"Banner {'activado' if banner.activo else 'desactivado'}.")
    return redirect("panel_banners")


@solo_admin
@require_POST
def panel_banner_borrar(request, pk):
    banner = get_object_or_404(Banner, pk=pk)
    banner.delete()
    messages.success(request, "Banner eliminado.")
    return redirect("panel_banners")
@solo_admin
def panel_clasificacion(request):
    form_franquicia = FranquiciaForm(prefix="f")
    form_tipo = TipoForm(prefix="t")

    if request.method == "POST":
        if "guardar_franquicia" in request.POST:
            form_franquicia = FranquiciaForm(request.POST, prefix="f")
            if form_franquicia.is_valid():
                form_franquicia.save()
                messages.success(request, "Franquicia creada.")
                return redirect("panel_clasificacion")
        elif "guardar_tipo" in request.POST:
            form_tipo = TipoForm(request.POST, prefix="t")
            if form_tipo.is_valid():
                form_tipo.save()
                messages.success(request, "Tipo de producto creado.")
                return redirect("panel_clasificacion")

    return render(request, "tienda/panel_clasificacion.html", {
        "seccion": "clasificacion",
        "form_franquicia": form_franquicia,
        "form_tipo": form_tipo,
        "franquicias": Franquicia.objects.annotate(total=Count("productos")),
        "tipos": TipoProducto.objects.annotate(total=Count("productos")),
    })
@solo_admin
def panel_venta_fisica(request):
    productos = (
        Producto.objects.filter(activo=True, stock__gt=0)
        .select_related("franquicia", "tipo")
        .order_by("nombre")
    )
    if request.method == "POST":
        metodo = request.POST.get("metodo_pago", "")
        cliente = " ".join(request.POST.get("cliente", "").split())[:200]
        referencia = request.POST.get("referencia", "").strip()[:100]
        confirmada = request.POST.get("transferencia_confirmada") == "on"

        try:
            lineas = []
            for p in productos:
                cant = int(request.POST.get(f"cant_{p.pk}") or 0)
                if cant > 0:
                    texto = request.POST.get(f"precio_{p.pk}", "").replace(",", ".").strip()
                    precio = Decimal(texto) if texto else p.precio
                    lineas.append((p.pk, cant, precio))

            if metodo not in ("efectivo", "transferencia"):
                raise ValidationError("Elige cómo pagó el cliente.")
            if metodo == "transferencia" and not confirmada:
                raise ValidationError("Confirma que ya recibiste la transferencia.")

            recibido = None
            if metodo == "efectivo":
                texto = request.POST.get("recibido", "").replace(",", ".").strip()
                if texto:
                    recibido = Decimal(texto)

            pedido = registrar_venta_fisica(
                lineas, metodo,
                usuario=request.user,
                cliente=cliente,
                monto_recibido=recibido,
                referencia=referencia,
            )
        except (ValueError, InvalidOperation):
            messages.error(request, "Revisa las cantidades y los precios.")
        except ValidationError as e:
            messages.error(request, e.messages[0])
        else:
            messages.success(request, f"Venta #{pedido.pk} registrada: ${pedido.total}")
            return redirect("panel_venta_ticket", pk=pedido.pk)
        return redirect("panel_venta_fisica")

    return render(request, "tienda/panel_venta_fisica.html", {
        "seccion": "venta_fisica",
        "productos": productos,
    })
@solo_admin
def panel_venta_nueva(request):
    previo = {"items": [], "metodo": "efectivo", "cliente": ""}

    if request.method == "POST":
        ids = request.POST.getlist("producto_id")
        cantidades = request.POST.getlist("cantidad")
        metodo = request.POST.get("metodo", "")
        cliente = " ".join(request.POST.get("cliente", "").split())[:200]
        referencia = request.POST.get("referencia", "").strip()[:100]
        confirmada = request.POST.get("transferencia_confirmada") == "on"

        lineas = {}
        for pid, cant in zip(ids, cantidades):
            if pid.isdigit() and cant.isdigit() and int(cant) > 0:
                lineas[int(pid)] = lineas.get(int(pid), 0) + int(cant)

        previo = {
            "items": [{"id": pid, "cantidad": c} for pid, c in lineas.items()],
            "metodo": metodo if metodo in ("efectivo", "transferencia") else "efectivo",
            "cliente": cliente,
        }

        error = None
        recibido = None
        if not lineas:
            error = "Agrega al menos un producto."
        elif metodo not in ("efectivo", "transferencia"):
            error = "Elige cómo pagó el cliente."
        elif metodo == "transferencia" and not confirmada:
            error = "Confirma que ya recibiste la transferencia."
        elif metodo == "efectivo":
            texto = request.POST.get("recibido", "").replace(",", ".").strip()
            if texto:
                try:
                    recibido = Decimal(texto)
                except InvalidOperation:
                    error = "El monto recibido no es válido."

        if not error:
            try:
                venta = registrar_venta_fisica(
                    [(pid, cant, None) for pid, cant in lineas.items()],
                    metodo_pago=metodo,
                    usuario=request.user,
                    cliente=cliente,
                    monto_recibido=recibido,
                    referencia=referencia,
                )
            except ValidationError as e:
                error = " ".join(e.messages)
            else:
                messages.success(request, f"Venta #{venta.pk} registrada.")
                return redirect("panel_venta_ticket", pk=venta.pk)

        messages.error(request, error)

    productos = (
        Producto.objects.filter(activo=True, stock__gt=0)
        .select_related("franquicia", "tipo")
        .order_by("nombre")
    )
    catalogo = [
        {
            "id": p.pk,
            "nombre": p.nombre,
            "precio": str(p.precio),
            "stock": p.stock,
            "detalle": " · ".join(
                x for x in (str(p.tipo) if p.tipo else "", str(p.franquicia) if p.franquicia else "") if x
            ),
        }
        for p in productos
    ]
    return render(request, "tienda/panel_venta_nueva.html", {
        "seccion": "ventas",
        "catalogo": catalogo,
        "previo": previo,
    })
@solo_admin
def panel_venta_ticket(request, pk):
    venta = get_object_or_404(
        Pedido.objects.prefetch_related("items"), pk=pk, canal=Pedido.Canal.FISICO
    )
    return render(request, "tienda/panel_venta_ticket.html", {
        "venta": venta,
        "seccion": "ventas",
    })


@solo_admin
def panel_ventas(request):
    hoy = timezone.localdate()
    try:
        fecha = date.fromisoformat(request.GET.get("fecha", ""))
    except ValueError:
        fecha = hoy

    ventas = (
        Pedido.objects.filter(canal=Pedido.Canal.FISICO, creado__date=fecha)
        .select_related("usuario")
        .prefetch_related("items")
        .order_by("-creado")
    )
    validas = ventas.exclude(estado=Pedido.Estado.CANCELADO).order_by()
    por_metodo = {
        fila["metodo_pago"]: fila
        for fila in validas.values("metodo_pago").annotate(total=Sum("total"), n=Count("id"))
    }
    vacio = {"total": 0, "n": 0}

    return render(request, "tienda/panel_ventas.html", {
        "seccion": "ventas",
        "fecha": fecha,
        "hoy": hoy,
        "ventas": ventas,
        "efectivo": por_metodo.get("efectivo", vacio),
        "transferencia": por_metodo.get("transferencia", vacio),
        "total_dia": validas.aggregate(t=Sum("total"))["t"] or 0,
        "n_anuladas": ventas.filter(estado=Pedido.Estado.CANCELADO).count(),
    })


@solo_admin
@require_POST
def panel_venta_anular(request, pk):
    venta = get_object_or_404(Pedido, pk=pk, canal=Pedido.Canal.FISICO)
    if venta.cancelar():
        messages.success(
            request,
            f"Venta #{venta.pk} anulada y stock devuelto. Recuerda devolver el dinero al cliente.",
        )
    fecha = timezone.localtime(venta.creado).date().isoformat()
    return redirect(f"{reverse('panel_ventas')}?fecha={fecha}")