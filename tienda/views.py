import hashlib
import hmac
import json
import re
import uuid
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps
from urllib import error as urlerror, request as urlrequest
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, ProtectedError, Q, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .forms import (
    BannerForm, CategoriaForm, CondicionForm, CondicionNombreForm, DireccionForm,
    FranquiciaForm, LineaForm, PedidoForm, ProductoForm, TipoForm,
)
from .models import (
    Banner, Categoria, Condicion, Direccion, Franquicia, Linea, Pedido,
    PedidoItem, Producto, TipoProducto, registrar_venta_fisica,
)


# ======================================================================
# Catálogo
# ======================================================================

def lista_productos(request):
    activos = Producto.objects.filter(activo=True)
    productos = activos.select_related(
        "franquicia", "tipo", "linea", "condicion"
    ).order_by("-creado")

    def a_numero(valor):
        return int(valor) if valor.isdigit() else None

    def buscar(modelo, clave):
        pk = a_numero(request.GET.get(clave, ""))
        return modelo.objects.filter(pk=pk).first() if pk else None

    tipo_actual = buscar(TipoProducto, "tipo")
    franquicia_actual = buscar(Franquicia, "franquicia")
    linea_actual = buscar(Linea, "linea")
    busqueda = request.GET.get("q", "").strip()

    if tipo_actual:
        productos = productos.filter(tipo=tipo_actual)
    if franquicia_actual:
        productos = productos.filter(franquicia=franquicia_actual)
    if linea_actual:
        productos = productos.filter(linea=linea_actual)
    if busqueda:
        productos = productos.filter(nombre__icontains=busqueda)

    def url(**params):
        params = {k: v for k, v in params.items() if v}
        return ("?" + urlencode(params)) if params else reverse("lista_productos")

    # --- Menú lateral: un elemento por tipo, con su submenú ---
    por_tipo = {
        fila["tipo_id"]: fila["n"]
        for fila in activos.values("tipo_id").annotate(n=Count("id")).order_by()
    }
    franquicias_por_tipo = {}
    for fila in (
        activos.exclude(franquicia=None)
        .values("tipo_id", "franquicia_id", "franquicia__nombre")
        .annotate(n=Count("id"))
        .order_by("franquicia__nombre")
    ):
        franquicias_por_tipo.setdefault(fila["tipo_id"], []).append({
            "nombre": fila["franquicia__nombre"],
            "n": fila["n"],
            "url": url(tipo=fila["tipo_id"], franquicia=fila["franquicia_id"]),
        })
    lineas_por_tipo = {}
    for fila in (
        activos.exclude(linea=None)
        .values("tipo_id", "linea_id", "linea__nombre")
        .annotate(n=Count("id"))
        .order_by("linea__nombre")
    ):
        lineas_por_tipo.setdefault(fila["tipo_id"], []).append({
            "nombre": fila["linea__nombre"],
            "n": fila["n"],
            "url": url(tipo=fila["tipo_id"], linea=fila["linea_id"]),
        })

    menu = []
    for t in TipoProducto.objects.all():
        if not por_tipo.get(t.pk):
            continue
        franquicias = (
            franquicias_por_tipo.get(t.pk, [])
            if t.submenu in ("franquicias", "ambas") else []
        )
        lineas = (
            lineas_por_tipo.get(t.pk, [])
            if t.submenu in ("lineas", "ambas") else []
        )
        menu.append({
            "nombre": t.nombre,
            "n": por_tipo[t.pk],
            "url": url(tipo=t.pk),
            "activo": tipo_actual is not None and t.pk == tipo_actual.pk,
            "franquicias": franquicias,
            "lineas": lineas,
            "hay_submenu": bool(franquicias or lineas),
        })

    # --- Portada: banners y nuevos lanzamientos solo sin filtros ni búsqueda ---
    es_inicio = not (tipo_actual or franquicia_actual or linea_actual or busqueda)
    limite_nuevo = timezone.now() - timedelta(days=14)
    banners = []
    nuevos = []
    if es_inicio:
        banners = Banner.objects.filter(activo=True)
        nuevos = list(productos[:10])
        for p in nuevos:
            p.es_nuevo = p.creado >= limite_nuevo

    # --- Título y páginas ---
    if busqueda:
        titulo_lista = f"Resultados para «{busqueda}»"
    else:
        partes = [str(x) for x in (tipo_actual, linea_actual, franquicia_actual) if x]
        titulo_lista = " › ".join(partes) or "Todos los productos"

    filtros = {
        "tipo": tipo_actual.pk if tipo_actual else None,
        "franquicia": franquicia_actual.pk if franquicia_actual else None,
        "linea": linea_actual.pk if linea_actual else None,
        "q": busqueda,
    }
    filtros = {k: v for k, v in filtros.items() if v}
    prefijo = ("?" + urlencode(filtros) + "&") if filtros else "?"

    pagina = Paginator(productos, 12).get_page(request.GET.get("pagina"))
    productos_pagina = list(pagina)
    for p in productos_pagina:
        p.es_nuevo = p.creado >= limite_nuevo

    return render(request, "tienda/lista.html", {
        "productos": productos_pagina,
        "pagina": pagina,
        "prefijo": prefijo,
        "menu": menu,
        "es_inicio": es_inicio,
        "banners": banners,
        "nuevos": nuevos,
        "busqueda": busqueda,
        "titulo_lista": titulo_lista,
    })


def detalle_producto(request, pk):
    producto = get_object_or_404(
        Producto.objects.select_related("franquicia", "tipo", "linea", "condicion")
        .prefetch_related("imagenes"),
        pk=pk, activo=True,
    )
    fotos = []
    if producto.imagen:
        fotos.append(producto.imagen)
    fotos.extend(extra.imagen for extra in producto.imagenes.all())
    return render(request, "tienda/detalle.html", {"producto": producto, "fotos": fotos})


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


# ======================================================================
# Carrito y checkout
# ======================================================================

MAX_DIRECCIONES = 10


def obtener_items_carrito(carrito):
    productos = Producto.objects.filter(pk__in=carrito.keys(), activo=True)
    items = []
    total = 0
    for producto in productos:
        cantidad = carrito[str(producto.pk)]
        subtotal = producto.precio_final * cantidad
        total += subtotal
        items.append({
            "producto": producto,
            "cantidad": cantidad,
            "subtotal": subtotal,
        })
    return items, total


def pedido_del_cliente(request, pk):
    """Devuelve el pedido solo a su dueño, o al invitado que lo creó o rastreó en este navegador."""
    pedido = get_object_or_404(Pedido, pk=pk, canal=Pedido.Canal.ONLINE)
    if request.user.is_authenticated and pedido.usuario_id == request.user.pk:
        return pedido
    if pedido.usuario_id is None and pk in request.session.get("pedidos_invitado", []):
        return pedido
    raise Http404


def _volver(request, pedido):
    """Con cuenta regresa a Mis pedidos; sin cuenta, a la página del pedido."""
    if request.user.is_authenticated:
        return redirect("mis_pedidos")
    return redirect("pedido_confirmado", pk=pedido.pk)


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


def _datos_iniciales(usuario, direcciones):
    inicial = {"email": usuario.email, "nombre": usuario.get_full_name()}
    principal = next((d for d in direcciones if d.principal), direcciones[0] if direcciones else None)
    if principal:
        inicial.update({
            "nombre": principal.nombre,
            "telefono": principal.telefono,
            "codigo_postal": principal.codigo_postal,
            "entidad": principal.entidad,
            "ciudad": principal.ciudad,
            "colonia": principal.colonia,
            "calle": principal.calle,
            "referencia": principal.referencia,
        })
    return inicial


def checkout(request):
    carrito = request.session.get("carrito", {})
    items, total = obtener_items_carrito(carrito)
    if not items:
        return redirect("ver_carrito")

    autenticado = request.user.is_authenticated
    direcciones = list(request.user.direcciones.all()) if autenticado else []

    if request.method == "POST":
        form = PedidoForm(request.POST)
        guardar = autenticado and request.POST.get("guardar_direccion") == "on"
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
                    total_real += producto.precio_final * item["cantidad"]

                pedido = form.save(commit=False)
                pedido.usuario = request.user if autenticado else None
                pedido.total = total_real
                pedido.save()

                for item in items:
                    producto = productos_por_id[item["producto"].pk]
                    PedidoItem.objects.create(
                        pedido=pedido,
                        producto=producto,
                        nombre=producto.nombre,
                        precio=producto.precio_final,
                        cantidad=item["cantidad"],
                    )
                    producto.stock -= item["cantidad"]
                    producto.save(update_fields=["stock"])

                if guardar and len(direcciones) < MAX_DIRECCIONES:
                    Direccion.objects.get_or_create(
                        usuario=request.user,
                        codigo_postal=pedido.codigo_postal,
                        calle=pedido.calle,
                        colonia=pedido.colonia,
                        defaults={
                            "nombre": pedido.nombre,
                            "telefono": pedido.telefono,
                            "ciudad": pedido.ciudad,
                            "entidad": pedido.entidad,
                            "referencia": pedido.referencia,
                            "principal": not direcciones,
                        },
                    )

            request.session["carrito"] = {}
            if not autenticado:
                # Sin cuenta, el navegador recuerda qué pedidos creó.
                ids = request.session.get("pedidos_invitado", [])
                request.session["pedidos_invitado"] = (ids + [pedido.pk])[-20:]
            # Si ya conectaste los correos, deja aquí: correo_pedido_recibido(pedido)
            return redirect("pedido_confirmado", pk=pedido.pk)
    else:
        form = PedidoForm(initial=_datos_iniciales(request.user, direcciones) if autenticado else None)

    return render(request, "tienda/checkout.html", {
        "form": form,
        "items": items,
        "total": total,
        "direcciones": direcciones,
        "direcciones_json": [
            {
                "id": d.pk, "nombre": d.nombre, "telefono": d.telefono,
                "codigo_postal": d.codigo_postal, "entidad": d.entidad,
                "ciudad": d.ciudad, "colonia": d.colonia, "calle": d.calle,
                "referencia": d.referencia,
            }
            for d in direcciones
        ],
    })


# ======================================================================
# Pedidos del cliente (con cuenta o como invitado)
# ======================================================================

def pedido_confirmado(request, pk):
    pedido = pedido_del_cliente(request, pk)
    transferencia = {
        "banco": settings.TRANSFER_BANCO,
        "beneficiario": settings.TRANSFER_BENEFICIARIO,
        "clabe": settings.TRANSFER_CLABE,
    }
    return render(request, "tienda/pedido_confirmado.html", {
        "pedido": pedido,
        "transferencia": transferencia,
    })


@login_required
def mis_pedidos(request):
    pedidos = Pedido.objects.filter(usuario=request.user).prefetch_related("items")
    return render(request, "tienda/mis_pedidos.html", {"pedidos": pedidos})


@require_POST
def cancelar_mi_pedido(request, pk):
    pedido = pedido_del_cliente(request, pk)
    if pedido.puede_cancelar:
        pedido.cancelar()
        messages.success(request, f"Pedido #{pedido.pk} cancelado.")
    else:
        messages.warning(request, "Solo se pueden cancelar pedidos pendientes de pago.")
    return _volver(request, pedido)


@require_POST
def solicitar_cancelacion(request, pk):
    pedido = pedido_del_cliente(request, pk)
    if pedido.puede_solicitar_cancelacion:
        pedido.cancelacion_solicitada = True
        pedido.save(update_fields=["cancelacion_solicitada"])
        messages.success(
            request,
            "Recibimos tu solicitud de cancelación. Te contactaremos para el reembolso.",
        )
    return _volver(request, pedido)


def editar_envio(request, pk):
    pedido = pedido_del_cliente(request, pk)
    if not pedido.puede_editar_envio:
        messages.warning(request, "Este pedido ya no se puede modificar.")
        return _volver(request, pedido)

    if request.method == "POST":
        form = PedidoForm(request.POST, instance=pedido)
        if form.is_valid():
            cambios = form.save(commit=False)
            cambios.save(update_fields=list(form.fields))
            messages.success(request, "Datos de envío actualizados.")
            return _volver(request, pedido)
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


@require_POST
def reportar_transferencia(request, pk):
    pedido = pedido_del_cliente(request, pk)
    if pedido.estado != Pedido.Estado.PENDIENTE:
        return redirect("pedido_confirmado", pk=pedido.pk)

    referencia = " ".join(request.POST.get("referencia", "").split())[:100]
    if not referencia:
        messages.error(request, "Escribe la clave de rastreo de tu transferencia.")
        return redirect("pedido_confirmado", pk=pedido.pk)

    # Una misma transferencia no puede usarse para varios pedidos.
    if Pedido.objects.filter(referencia_pago__iexact=referencia).exclude(pk=pedido.pk).exists():
        messages.error(
            request,
            "Esa clave de rastreo ya está registrada en otro pedido. "
            "Si crees que es un error, contáctanos.",
        )
        return redirect("pedido_confirmado", pk=pedido.pk)

    pedido.metodo_pago = "transferencia"
    pedido.referencia_pago = referencia
    pedido.estado = Pedido.Estado.POR_VERIFICAR
    pedido.save(update_fields=["metodo_pago", "referencia_pago", "estado"])
    messages.success(
        request,
        "Recibimos tu aviso. Revisaremos tu transferencia y te confirmaremos el pago.",
    )
    return redirect("pedido_confirmado", pk=pedido.pk)


def _solo_digitos(texto):
    return re.sub(r"\D", "", texto or "")[-10:]


def rastrear_pedido(request):
    error = ""
    codigo = telefono = ""
    if request.method == "POST":
        codigo = request.POST.get("codigo", "").upper().replace("-", "").replace(" ", "")
        telefono = request.POST.get("telefono", "")
        pedido = Pedido.objects.filter(codigo=codigo, canal=Pedido.Canal.ONLINE).first()
        if pedido and _solo_digitos(pedido.telefono) == _solo_digitos(telefono):
            if pedido.usuario_id:
                messages.info(request, "Ese pedido está ligado a una cuenta. Inicia sesión para verlo.")
                destino = reverse("pedido_confirmado", args=[pedido.pk])
                return redirect(f"{reverse('login')}?next={destino}")
            ids = request.session.get("pedidos_invitado", [])
            if pedido.pk not in ids:
                request.session["pedidos_invitado"] = (ids + [pedido.pk])[-20:]
            return redirect("pedido_confirmado", pk=pedido.pk)
        error = "No encontramos un pedido con ese código y teléfono. Revisa los datos."
    return render(request, "tienda/rastrear_pedido.html", {
        "error": error,
        "codigo": codigo,
        "telefono": telefono,
    })


# ======================================================================
# Pagos con Mercado Pago (API de Orders)
# ======================================================================

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


def pagar_pedido(request, pk):
    pedido = pedido_del_cliente(request, pk)
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


def pago_retorno(request):
    # El regreso solo sirve para ubicar el pedido; el estado real se consulta a Mercado Pago.
    try:
        pedido = pedido_del_cliente(request, int(request.GET.get("external_reference")))
    except (TypeError, ValueError, Http404):
        messages.warning(request, "No pudimos identificar tu pedido.")
        return redirect("mis_pedidos" if request.user.is_authenticated else "lista_productos")

    if pedido.mp_order_id:
        sincronizar_pago(pedido.mp_order_id)
    return redirect("pedido_confirmado", pk=pedido.pk)


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


# ======================================================================
# Direcciones del cliente (requieren cuenta)
# ======================================================================

@login_required
def mis_direcciones(request):
    return render(request, "tienda/mis_direcciones.html", {
        "direcciones": request.user.direcciones.all(),
        "maximo": MAX_DIRECCIONES,
    })


@login_required
def direccion_form(request, pk=None):
    direccion = get_object_or_404(Direccion, pk=pk, usuario=request.user) if pk else None
    if direccion is None and request.user.direcciones.count() >= MAX_DIRECCIONES:
        messages.error(request, f"Puedes guardar hasta {MAX_DIRECCIONES} direcciones.")
        return redirect("mis_direcciones")

    if request.method == "POST":
        form = DireccionForm(request.POST, instance=direccion)
        if form.is_valid():
            nueva = form.save(commit=False)
            nueva.usuario = request.user
            if not request.user.direcciones.exclude(pk=nueva.pk).exists():
                nueva.principal = True  # la primera siempre es la principal
            nueva.save()
            messages.success(request, "Dirección guardada.")
            return redirect("mis_direcciones")
    else:
        form = DireccionForm(instance=direccion)
    return render(request, "tienda/direccion_form.html", {"form": form, "direccion": direccion})


@login_required
@require_POST
def direccion_principal(request, pk):
    direccion = get_object_or_404(Direccion, pk=pk, usuario=request.user)
    direccion.principal = True
    direccion.save()
    messages.success(request, "Ahora es tu dirección principal.")
    return redirect("mis_direcciones")


@login_required
@require_POST
def direccion_borrar(request, pk):
    direccion = get_object_or_404(Direccion, pk=pk, usuario=request.user)
    era_principal = direccion.principal
    direccion.delete()
    if era_principal:
        otra = request.user.direcciones.first()
        if otra:
            otra.principal = True
            otra.save()
    messages.success(request, "Dirección eliminada.")
    return redirect("mis_direcciones")


# ======================================================================
# Panel de administración
# ======================================================================

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
        "por_verificar": conteos.get("por_verificar", 0),
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
@require_POST
def panel_resolver_transferencia(request, pk):
    pedido = Pedido.objects.filter(
        pk=pk, canal=Pedido.Canal.ONLINE, estado=Pedido.Estado.POR_VERIFICAR
    ).first()
    if pedido is None:
        messages.warning(request, "Ese pedido ya no está por verificar.")
    elif request.POST.get("accion") == "confirmar":
        pedido.estado = Pedido.Estado.PAGADO
        pedido.metodo_pago = "transferencia"
        pedido.save(update_fields=["estado", "metodo_pago"])
        # Si ya conectaste los correos, agrega aquí: correo_pago_confirmado(pedido)
        messages.success(request, f"Pago del pedido #{pedido.pk} confirmado.")
    elif request.POST.get("accion") == "rechazar":
        pedido.estado = Pedido.Estado.PENDIENTE
        pedido.referencia_pago = ""
        pedido.save(update_fields=["estado", "referencia_pago"])
        messages.warning(
            request,
            f"El pedido #{pedido.pk} volvió a pendiente: el cliente puede pagar de nuevo.",
        )
    return redirect(f"{reverse('panel_pedidos')}?estado=por_verificar")


@solo_admin
def panel_productos(request):
    productos = (Producto.objects
                 .select_related("franquicia", "tipo", "linea")
                 .order_by("-creado"))

    # Búsqueda libre: cada palabra debe aparecer en algún campo
    busqueda = request.GET.get("q", "").strip()
    if busqueda:
        for palabra in busqueda.split():
            variantes = {palabra}
            if len(palabra) > 3 and palabra.lower().endswith("s"):
                variantes.add(palabra[:-1])
            filtro = Q()
            for v in variantes:
                filtro |= (
                    Q(nombre__icontains=v)
                    | Q(franquicia__nombre__icontains=v)
                    | Q(tipo__nombre__icontains=v)
                    | Q(linea__nombre__icontains=v)
                )
            productos = productos.filter(filtro)

    # Tipo (selector)
    tipo = request.GET.get("tipo", "")
    if tipo.isdigit():
        productos = productos.filter(tipo_id=tipo)
    else:
        tipo = ""

    # Franquicia (texto libre con sugerencias)
    franquicia = request.GET.get("franquicia", "").strip()
    if franquicia:
        productos = productos.filter(franquicia__nombre__icontains=franquicia)

    # Solo las líneas que tienen productos con los filtros de arriba
    lineas = (Linea.objects
              .filter(productos__in=productos)
              .distinct()
              .order_by("nombre"))

    linea = request.GET.get("linea", "")
    if linea.isdigit() and lineas.filter(pk=linea).exists():
        productos = productos.filter(linea_id=linea)
    else:
        linea = ""

    # Cuántos pedidos tiene cada producto (se calcula al final, ya filtrado)
    productos = productos.annotate(n_pedidos=Count("pedidoitem"))

    return render(request, "tienda/panel_productos.html", {
        "seccion": "productos",
        "productos": productos,
        "busqueda": busqueda,
        "tipos": TipoProducto.objects.order_by("nombre"),
        "franquicias": Franquicia.objects.order_by("nombre").values_list("nombre", flat=True),
        "lineas": lineas,
        "tipo_sel": int(tipo) if tipo else None,
        "franquicia_sel": franquicia,
        "linea_sel": int(linea) if linea else None,
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
        "lineas": list(Linea.objects.values_list("nombre", flat=True)),
        "lineas_info": list(Linea.objects.values("nombre", "tipo__nombre", "franquicia__nombre")),
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
    form_condicion = CondicionForm(prefix="c")

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
        elif "guardar_condicion" in request.POST:
            form_condicion = CondicionForm(request.POST, prefix="c")
            if form_condicion.is_valid():
                form_condicion.save()
                messages.success(request, "Estado agregado.")
                return redirect("panel_clasificacion")
        elif "guardar_submenu" in request.POST:
            tipo_id = request.POST.get("tipo_id", "")
            valor = request.POST.get("submenu", "")
            tipo = TipoProducto.objects.filter(pk=int(tipo_id)).first() if tipo_id.isdigit() else None
            if tipo and valor in TipoProducto.Submenu.values:
                tipo.submenu = valor
                tipo.save(update_fields=["submenu"])
                messages.success(request, f"Menú de «{tipo.nombre}» actualizado.")
            return redirect("panel_clasificacion")

    return render(request, "tienda/panel_clasificacion.html", {
        "seccion": "clasificacion",
        "form_franquicia": form_franquicia,
        "form_tipo": form_tipo,
        "form_condicion": form_condicion,
        "franquicias": Franquicia.objects.annotate(total=Count("productos")),
        "tipos": TipoProducto.objects.annotate(total=Count("productos")).prefetch_related("condiciones"),
        "submenus": TipoProducto.Submenu.choices,
    })


ETIQUETAS = {
    "franquicia": (Franquicia, FranquiciaForm, "franquicia"),
    "tipo": (TipoProducto, TipoForm, "tipo de producto"),
    "linea": (Linea, LineaForm, "línea o marca"),
    "estado": (Condicion, CondicionNombreForm, "estado"),
}


@solo_admin
def panel_etiqueta_editar(request, clave, pk):
    if clave not in ETIQUETAS:
        raise Http404
    modelo, formulario, nombre_legible = ETIQUETAS[clave]
    objeto = get_object_or_404(modelo, pk=pk)

    if request.method == "POST":
        form = formulario(request.POST, instance=objeto)
        if form.is_valid():
            form.save()
            messages.success(request, f"Cambio guardado: «{objeto}».")
            return redirect("panel_clasificacion")
    else:
        form = formulario(instance=objeto)
    return render(request, "tienda/panel_etiqueta_form.html", {
        "seccion": "clasificacion",
        "form": form,
        "objeto": objeto,
        "nombre_legible": nombre_legible,
        "usos": objeto.productos.count(),
    })


@solo_admin
@require_POST
def panel_etiqueta_borrar(request, clave, pk):
    if clave not in ETIQUETAS:
        raise Http404
    modelo, _formulario, nombre_legible = ETIQUETAS[clave]
    objeto = get_object_or_404(modelo, pk=pk)
    etiqueta = str(objeto)

    usos = objeto.productos.count()
    if usos:
        messages.error(
            request,
            f"No se puede eliminar «{etiqueta}»: la usan {usos} producto(s). "
            "Cámbiales ese dato primero.",
        )
    else:
        try:
            objeto.delete()
        except ProtectedError:
            messages.error(request, f"No se puede eliminar «{etiqueta}» porque está en uso.")
        else:
            messages.success(request, f"Se eliminó {nombre_legible} «{etiqueta}».")
    return redirect("panel_clasificacion")


# ======================================================================
# Ventas físicas
# ======================================================================

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
                    precio = Decimal(texto) if texto else p.precio_final
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
@solo_admin
@require_POST
def panel_producto_borrar(request, pk):
    producto = get_object_or_404(Producto, pk=pk)
    nombre = producto.nombre
    archivos = [producto.imagen] if producto.imagen else []
    archivos += [extra.imagen for extra in producto.imagenes.all()]

    with transaction.atomic():
        producto.delete()  # los pedidos anteriores conservan su historial
    for archivo in archivos:
        archivo.delete(save=False)

    messages.success(request, f"Producto «{nombre}» eliminado.")
    return redirect("panel_productos")


@solo_admin
def panel_estado_editar(request, pk):
    estado = get_object_or_404(Condicion.objects.select_related("tipo"), pk=pk)
    if request.method == "POST":
        form = CondicionNombreForm(request.POST, instance=estado)
        if form.is_valid():
            form.save()
            messages.success(request, "Estado actualizado.")
            return redirect("panel_clasificacion")
    else:
        form = CondicionNombreForm(instance=estado)
    return render(request, "tienda/panel_estado_form.html", {
        "seccion": "clasificacion",
        "form": form,
        "estado": estado,
        "usos": estado.productos.count(),
    })


@solo_admin
@require_POST
def panel_estado_borrar(request, pk):
    estado = get_object_or_404(Condicion.objects.select_related("tipo"), pk=pk)
    etiqueta = f"{estado.tipo} · {estado.nombre}"
    with transaction.atomic():
        afectados = estado.productos.update(condicion=None)
        estado.delete()
    if afectados:
        messages.success(
            request,
            f"Estado «{etiqueta}» eliminado. {afectados} producto(s) quedaron sin estado.",
        )
    else:
        messages.success(request, f"Estado «{etiqueta}» eliminado.")
    return redirect("panel_clasificacion")