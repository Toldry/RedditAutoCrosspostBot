"""Translates strings according to the target subreddit"""

# I've intentionally decided not to use `gettext` or `i18n` because their implementations appear to be overly complex for my simple use case

DEFAULT_LANGUAGE = 'en'

# I used google translate for some of these as placeholders, gonna need native speakers to do proper translations here
translations = {
    'POST_SUFFIX_TEXT':{
        'en':'''

---
^^🤖 ^^this ^^comment ^^was ^^written ^^by ^^a ^^bot. ^^beep ^^boop ^^🤖

^^feel ^^welcome ^^to ^^respond ^^'Bad ^^bot'/'Good ^^bot', ^^it's ^^useful ^^feedback. [^^github](https://github.com/Toldry/RedditAutoCrosspostBot)'''
,
    'es':'''

--- 
^^🤖 ^^este ^^comentario ^^fue ^^escrito ^^por ^^un ^^bot. ^^bip ^^boop ^^🤖

^^siéntase ^^bienvenido ^^a ^^responder ^^'Mal ^^bot' ^^/ ^^'Buen ^^bot', ^^es ^^una ^^retroalimentación ^^útil'''
,
    'de':'''

---
^^🤖 ^^Dieser ^^Kommentar ^^wurde ^^von ^^einem ^^Bot ^^geschrieben. ^^Beep ^^Boop ^^🤖

^^Gerne ^^kannst ^^du ^^mit ^^"Schlechter ^^Bot" ^^/ ^^"Guter ^^Bot" ^^antworten. ^^Es ^^ist ^^ein ^^nützliches ^^Feedback.'''
,
    'fr':'''

---
^^🤖 ^^ce ^^commentaire ^^a ^^été ^^écrit ^^par ^^un ^^bot. ^^bip ^^boop ^^🤖

^^N'hésitez ^^pas ^^à ^^répondre ^^'Mauvais ^^bot' ^^/ ^^'Bon ^^bot', ^^c'est ^^un ^^retour ^^utile'''
,
    'he':'''
    
---
^^🤖 ^^תגובה ^^זו ^^נכתבה ^^על ^^ידי ^^בוט. ^^ביפ ^^בופ ^^🤖

^^תרגישו ^^חופשי ^^להגיב ^^'בוט ^^רע' ^^/ ^^'בוט ^^טוב', ^^זה ^^משוב ^^שימושי'''
    },
    'RESPOND_TO_NEGATIVE_SENTIMENT':{
        'en':'''
Thanks for the feedback, would you mind detailing why this post/comment was inappropriate?

The creator of this bot will look at the responses and try to change the code to reduce incidences like these.'''
,
    'es':None,
    'de':None,
    'fr':None,
    'he':None,
    },
    'REPLY_TO_CROSSPOST':{
        'en':'''
I crossposted this from r/{source_subreddit} to r/{target_subreddit} after seeing [this decently upvoted **human-made**^^1 comment]({source_comment_permalink}) (score={source_comment_score}), that seems to suggest that this post would be a good fit here too.
    
I checked on [repostsleuth.com](https://repostsleuth.com/search?postId={source_submission_id})^^2 before crossposting, to make sure this wasn't already posted before in r/{target_subreddit}.

I also waited {timedelta_string}^^3 before crossposting, in case a human might've wanted to crosspost this themselves.

If you think this was a mistake, go ahead and downvote; I'll remove posts with negative scores.

---
^^1 ^^- ^^Assuming ^^/u/{source_comment_author_name} ^^is ^^human

^^2 ^^- ^^Might ^^not ^^work ^^for ^^some ^^types ^^of ^^submissions, ^^such ^^as ^^videos

^^3 ^^- ^^This ^^value ^^was ^^chosen ^^arbitrarily

''',
    'es':'''
Publiqué esto de r/{source_subreddit} a r/{target_subreddit} después de ver [este comentario **hecho por humanos** con una votación decente] ({source_comment_permalink}) (score={source_comment_score}), que parece Sugiero que esta publicación también encajaría bien aquí.
  
Si cree que esto fue un error, siga adelante y vote en contra; Eliminaré las publicaciones con puntuaciones negativas.
''',
    'de':'''
Ich habe diesen Pfosten von r/{source_subreddit} nach r/{target_subreddit} gekreuzt, nachdem ich [diesen anständig hoochgewählten und von Menschen gemachten Kommentar] ({source_comment_permalink}) (score={source_comment_score}) gesehen habe und mir dachte, dass dieser Beitrag auch hier gut passen würde.

Wenn du der Meinung bist, dass es hier nicht passt, downvote den Post. Ich werde Beiträge mit negativen Ergebnissen entfernen.
'''
,
    'fr':'''
J'ai croisé ceci de r/{source_subreddit} à r/{target_subreddit} après avoir vu [ce commentaire  fait par l'homme  décemment voté] ({source_comment_permalink}) (score={source_comment_score}), cela semble suggèrent que cet article conviendrait également ici.
  
Si vous pensez que c'était une erreur, allez-y et votez contre; Je supprimerai les messages avec des scores négatifs.
'''
    ,
    'he':'''
עשיתי קרוספוסט מ־ r/{source_subreddit} ל־ r/{target_subreddit} אחרי שראיתי את [הקומענט הזה **שנכתב על ידי בן אדם**^^1]({source_comment_permalink}) (ניקוד={source_comment_score}), שמרמז כי הפוסט הזה מתאים גם לכאן.
    
בדקתי ב־ [repostsleuth.com](https://repostsleuth.com/search?postId={source_submission_id})^^2 לפני שקריספסטתי, על מנת לוודא שלא כבר קריספסטו את זה ל־ r/{target_subreddit}.

בנוסף, חיכיתי {timedelta_string}^^3 לפני שקריספסטתי, במקרה ובן אדם היה רוצה לקרספסט את זה בעצמו.

אם לדכתך זו טעות, את.ה מוזמן להצביע־מטה; אסיר פוסטים עם ניקוד שלילי.

---
^^1 ^^- ^^Assuming ^^/u/{source_comment_author_name} ^^is ^^human
^^1 ^^- ^^בהנחה ^^ש ^^/u/{source_comment_author_name} ^^בן־אנוש

^^2 ^^- ^^אלול ^^לא ^^לעבוד ^^בסוגי ^^פוסטים ^^מסוימים, ^^כגון ^^סרטונים.

^^3 ^^- ^^ערך ^^זה ^^נבחר ^^באקראי

''',
    },
    'THE_USER_WHO_COMMENTED':{
    'en':'''the user who commented''',
    'es':None,
    'de':'''der Nutzer, der geantwortet hat''',
    'fr':None,
    'he':None,
    },
    'THATS_WHERE_WE_ARE':{
    'en':'''Yes, that's where we are.''',
    'es':None,
    'de':'''Ja, hier sind wir''',
    'fr':None,
    'he':'''כן, זה איפו שאנחנו.''',
    },
    'NONEXISTENT_SUBREDDIT':{
    'en':'''The subreddit r/{target_subreddit} does not exist. ''',
    'es':None,
    'de':'''Das Unter r/{target_subreddit} existiert nicht''',
    'fr':None,
    'he':'''הסאברעדיט r/{target_subreddit} לא קיים. ''',
    },
    'PROMPT_NONEXISTENT_SUBREDDIT_CREATION':{
    'en':'''Consider [creating it](/subreddits/create?name={target_subreddit}).''',
    'es':None,
    'de':'''Vielleicht [sollte man es erstellen](/subreddits/create?name={target_subreddit}).''',
    'fr':None,
    'he':'''תשקול [ליצור אותו](/subreddits/create?name={target_subreddit}).''',
    },
    'FOUND_POST_WITH_SAME_CONTENT':{
    'en':'''I found [this post]({same_content_post_url}) in r/{target_subreddit} with the same content as the current post.''',
    'es':None,
    'de':'''Ich habe [diesen Post]({same_content_post_url}) in r/{target_subreddit} gefunden, der den selben Link enthält.''',
    'fr':None,
    'he':'''מצאתי את  [הפוסט הזה]({same_content_post_url}) ב־ r/{target_subreddit} עם אותו התוכן כמו הפוסט הנוכחי.''',
    },
}

# TODO: use language detection algorithms to automate this
subreddit_language_map = {
    'ani_bm':'he',
    'israel':'he',
    'sabamba':'he',
    'hebrew' : 'he',
    'yeladim_kamoni':'he',
    'besederhavertipesh':'he',
    'okhavermugbal':'he',
    'ISR_historymemes':'he',
    'IshKiriatGat':'he',
    'ISR_copypasta':'he',
    'HayaLiSHabatz':'he',
    'hmmm_but_in_hebrew':'he',
    'yapanfasha':'he',
    'HibatGufotComedit':'he',
    'mischakim':'he',
    'TmonotMekolalot':'he',
    'ani_bmcirclejerk':'he',
    'Arsim':'he',
    'memim':'he',
    #
    'yo_elvr'           :'es',
    #
    'ich_iel'           :'de',
    'LTB_iel'           :'de',
    'schkreckl'         :'de',
    'wasletztepreis'    :'de',
    'OkBrudiMongo'      :'de',
    'GeschichtsMaimais' :'de',
    'de_IAmA': 'de',
    'Bundesliga': 'de',
    'de': 'de',
    'GermanAustriaberlin': 'de',
    'Switzerland': 'de',
    'Rammstein': 'de',
    'borussiadortmundwien': 'de',
    'Munich': 'de',
    'berlinsocialclub': 'de',
    'GermanPractice': 'de',
    'kreiswichsFragReddit': 'de',
    'SCHLAND': 'de',
    'GermanRap': 'de',
    'BUENZLI': 'de',
    'schweiz': 'de',
    'hamburg': 'de',
    'germanmusic': 'de',
    'frankfurt': 'de',
    'zurichEasy_German': 'de',
    'GermanFacts': 'de',
    'Dokumentationen': 'de',
    'cologne': 'de',
    'schalke04': 'de',
    'germantrees': 'de',
    'stuttgart': 'de',
    'de_podcasts': 'de',
    'germanpuns': 'de',
    'GermanMovies': 'de',
    'Wissenschaft': 'de',
    'dtm': 'de',
    'Kochen': 'de',
    'NettHier': 'de',
    #
    'moi_dlvv'          :'fr',
    'rance'             :'fr',
    #
}

subreddit_language_map=dict((k.lower(), v) for k,v in subreddit_language_map.items()) #change keys to lower-case for later string comparison

def get_translated_string(string_key, source_subreddit, add_suffix=True):
    language = DEFAULT_LANGUAGE
    source_subreddit = source_subreddit.lower()
    if source_subreddit in subreddit_language_map:
        source_subreddit_language = subreddit_language_map[source_subreddit]
        if source_subreddit_language in translations[string_key] and translations[string_key][source_subreddit_language] is not None:
            language = source_subreddit_language
    
    translated_string = translations[string_key][language]
    if add_suffix:
        suffix_language = DEFAULT_LANGUAGE
        if translations['POST_SUFFIX_TEXT'][language] is not None:
            suffix_language = language
        translated_string += translations['POST_SUFFIX_TEXT'][suffix_language]

    return translated_string

