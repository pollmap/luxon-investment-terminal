"""Small first-screen projection; heavy financial history loads on demand."""


def project(detail):
    selected={key:detail[key] for key in ('company',) if key in detail}
    return {'present':bool(detail),'detail':selected}
