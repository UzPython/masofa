UZBEKISTAN_LOCATIONS = {
    "Qoraqalpog'iston Respublikasi": [
        "Amudaryo", "Beruniy", "Bo'zatau", "Chimboy", "Ellikqal'a", "Kegeyli",
        "Mo'ynoq", "Nukus", "Qonliko'l", "Qorao'zak", "Qo'ng'irot", "Shumanay",
        "Taxtako'pir", "Taxiatosh", "To'rtko'l", "Xo'jayli",
    ],
    "Andijon viloyati": [
        "Andijon", "Asaka", "Baliqchi", "Bo'ston", "Buloqboshi", "Izboskan",
        "Jalaquduq", "Marhamat", "Oltinko'l", "Paxtaobod", "Qo'rg'ontepa",
        "Shahrixon", "Ulug'nor", "Xo'jaobod", "Xonobod",
    ],
    "Buxoro viloyati": [
        "Buxoro", "G'ijduvon", "Jondor", "Kogon", "Olot", "Peshku", "Qorako'l",
        "Qorovulbozor", "Romitan", "Shofirkon", "Vobkent",
    ],
    "Jizzax viloyati": [
        "Arnasoy", "Baxmal", "Do'stlik", "Forish", "G'allaorol", "Jizzax",
        "Mirzacho'l", "Paxtakor", "Yangiobod", "Zafarobod", "Zarbdor", "Zomin",
    ],
    "Qashqadaryo viloyati": [
        "Chiroqchi", "Dehqonobod", "G'uzor", "Kasbi", "Kitob", "Ko'kdala", "Koson",
        "Mirishkor", "Muborak", "Nishon", "Qamashi", "Qarshi", "Shahrisabz", "Yakkabog'",
    ],
    "Navoiy viloyati": [
        "Karmana", "Konimex", "Navbahor", "Nurota", "Qiziltepa", "Tomdi",
        "Uchquduq", "Xatirchi",
    ],
    "Namangan viloyati": [
        "Chortoq", "Chust", "Davlatobod", "Kosonsoy", "Mingbuloq", "Namangan",
        "Norin", "Pop", "To'raqo'rg'on", "Uychi", "Yangiqo'rg'on",
    ],
    "Samarqand viloyati": [
        "Bulung'ur", "Ishtixon", "Jomboy", "Kattaqo'rg'on", "Narpay", "Nurobod",
        "Oqdaryo", "Paxtachi", "Payariq", "Pastdarg'om", "Qo'shrabot", "Samarqand",
        "Toyloq", "Urgut",
    ],
    "Surxondaryo viloyati": [
        "Angor", "Bandixon", "Boysun", "Denov", "Jarqo'rg'on", "Muzrabot", "Oltinsoy",
        "Qiziriq", "Qumqo'rg'on", "Sariosiyo", "Sherobod", "Sho'rchi", "Termiz", "Uzun",
    ],
    "Sirdaryo viloyati": [
        "Boyovut", "Guliston", "Mirzaobod", "Oqoltin", "Sardoba", "Sayxunobod",
        "Sirdaryo", "Xovos",
    ],
    "Toshkent viloyati": [
        "Angren", "Bekobod", "Bo'ka", "Bo'stonliq", "Chinoz", "Ohangaron", "Oqqo'rg'on",
        "Olmaliq", "Parkent", "Piskent", "Quyichirchiq", "O'rtachirchiq", "Toshkent",
        "Yangiyo'l", "Yuqorichirchiq", "Zangiota",
    ],
    "Farg'ona viloyati": [
        "Bag'dod", "Beshariq", "Buvayda", "Dang'ara", "Farg'ona", "Furqat", "Oltiariq",
        "O'zbekiston", "Qo'shtepa", "Quva", "Rishton", "So'x", "Toshloq", "Uchko'prik",
        "Yozyovon",
    ],
    "Xorazm viloyati": [
        "Bog'ot", "Gurlan", "Hazorasp", "Xiva", "Xonqa", "Qo'shko'pir", "Shovot",
        "Tuproqqal'a", "Urganch", "Yangiariq", "Yangibozor",
    ],
    "Toshkent shahri": [
        "Bektemir", "Chilonzor", "Mirobod", "Mirzo Ulug'bek", "Olmazor", "Sergeli",
        "Shayxontohur", "Uchtepa", "Yakkasaroy", "Yangihayot", "Yashnobod", "Yunusobod",
    ],
}


def is_valid_location(region, district):
    return region in UZBEKISTAN_LOCATIONS and district in UZBEKISTAN_LOCATIONS[region]